"""真实 CPU ORT 检查 DLPack 元数据寿命；CUDA 故障注入不占 GPU。"""
import ctypes
import gc
import subprocess
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from flowocr.extract import ort_server as server


class InitializerLifetimeTests(unittest.TestCase):
    def test_metadata_allocation_failure_raises_before_capsule_creation(self):
        from unittest.mock import Mock
        runtime = SimpleNamespace(malloc=Mock(return_value=None), free=Mock())
        with patch.object(ctypes, 'CDLL', return_value=runtime), \
             patch.object(ctypes.pythonapi, 'PyCapsule_New') as capsule:
            with self.assertRaises(MemoryError):
                server._borrowed_float32_ortvalue(4096, (1024,), 1, 0)
        runtime.free.assert_not_called()
        capsule.assert_not_called()

    def test_session_retains_borrowed_initializer_after_python_owners_clear(self):
        import onnx
        from onnx import TensorProto, helper, numpy_helper
        data = np.arange(2048, dtype=np.float32).reshape(2, 1024)
        model = helper.make_model(helper.make_graph([
            helper.make_node('Add', ['x', 'w'], ['y'])], 'borrowed',
            [helper.make_tensor_value_info('x', TensorProto.FLOAT, [2, 1024])],
            [helper.make_tensor_value_info('y', TensorProto.FLOAT, [2, 1024])],
            [numpy_helper.from_array(data, 'w')]), opset_imports=[helper.make_opsetid('', 17)], ir_version=9)
        value = server._borrowed_float32_ortvalue(data.ctypes.data, data.shape, 1, 0)
        self.assertEqual(value.data_ptr(), data.ctypes.data)
        options = server.ort.SessionOptions()
        options.add_initializer('w', value)
        sessions = [server.ort.InferenceSession(model.SerializeToString(), options,
                    providers=['CPUExecutionProvider']) for _ in range(2)]
        del options, value
        gc.collect()
        for session in sessions:
            np.testing.assert_array_equal(session.run(None, {'x': np.ones_like(data)})[0], data + 1)
        del session
        # 最后的 native owner 在另一线程释放；data 持续存活。
        errors = []
        def release():
            try:
                sessions.clear()
                gc.collect()
            except BaseException as exc:
                errors.append(exc)
        thread = threading.Thread(target=release)
        thread.start()
        thread.join(5)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        np.testing.assert_array_equal(data, np.arange(2048, dtype=np.float32).reshape(2, 1024))

    def test_conversion_and_wrapper_failures_free_metadata_once(self):
        runtime = ctypes.CDLL('ucrtbase.dll' if sys.platform == 'win32' else None)
        runtime.malloc.argtypes, runtime.malloc.restype = [ctypes.c_size_t], ctypes.c_void_p
        runtime.free.argtypes, runtime.free.restype = [ctypes.c_void_p], None
        allocated, freed = [], []
        @ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_size_t)
        def allocate(size):
            address = runtime.malloc(size)
            allocated.append(address)
            return address
        @ctypes.CFUNCTYPE(None, ctypes.c_void_p)
        def free(address):
            freed.append(address)
            runtime.free(address)
        data = np.ones(1024, np.float32)
        for failure in ('capsule', 'convert', 'wrap'):
            before = len(allocated)
            with self.subTest(failure=failure), \
                 patch.object(ctypes, 'CDLL', return_value=SimpleNamespace(malloc=allocate, free=free)):
                if failure == 'capsule':
                    with patch.object(ctypes.pythonapi, 'PyCapsule_New', side_effect=MemoryError('capsule')):
                        with self.assertRaises(MemoryError):
                            server._borrowed_float32_ortvalue(data.ctypes.data, data.shape, 1, 0)
                elif failure == 'convert':
                    with self.assertRaises(Exception):
                        server._borrowed_float32_ortvalue(data.ctypes.data, data.shape, 999, 0)
                else:
                    with patch.object(server.ort, 'OrtValue', side_effect=RuntimeError('wrap')):
                        with self.assertRaises(RuntimeError):
                            server._borrowed_float32_ortvalue(data.ctypes.data, data.shape, 1, 0)
            self.assertEqual(len(allocated), before + 1)
            gc.collect()  # 包装失败的 mock 调用记录/异常帧也可能暂时持有 native value。
            self.assertEqual(len(freed), len(allocated))
        self.assertEqual(freed, allocated)

    def test_values_survive_until_interpreter_finalization(self):
        code = '''
import gc, numpy as np
from flowocr.extract.ort_server import _borrowed_float32_ortvalue
data = np.arange(2048, dtype=np.float32)
values = [_borrowed_float32_ortvalue(data.ctypes.data, data.shape, 1, 0) for _ in range(80)]
gc.collect()
for value in values:
    np.testing.assert_array_equal(value.numpy(), data)
print('reached normal interpreter exit', flush=True)
'''
        result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('reached normal interpreter exit', result.stdout)

    def test_partial_cuda_failure_releases_only_unpublished_buffer(self):
        import onnx
        from onnx import helper, numpy_helper
        graph = helper.make_graph([], 'weights', [], [], [
            numpy_helper.from_array(np.ones(1024, np.float32), name) for name in ('first', 'second')])
        model = helper.make_model(graph)
        for failure in ('copy', 'convert', 'allocate'):
            allocated, freed, copied = [], [], []
            def allocate(address, size):
                if failure == 'allocate' and allocated:
                    return 2
                ptr = 4096 * (len(allocated) + 1)
                ctypes.cast(address, ctypes.POINTER(ctypes.c_void_p))[0] = ptr
                allocated.append(ptr)
                return 0
            def copy(ptr, *args):
                copied.append(ptr.value)
                return 1 if failure == 'copy' and len(copied) == 2 else 0
            cuda = SimpleNamespace(cudaSetDevice=lambda _: 0, cudaMalloc=allocate, cudaMemcpy=copy,
                                   cudaFree=lambda ptr: freed.append(ptr.value) or 0)
            first, keep = object(), []
            with self.subTest(failure=failure), patch.object(onnx, 'load', return_value=model), \
                 patch.object(ctypes, 'CDLL', return_value=cuda), \
                 patch.object(server, '_borrowed_float32_ortvalue', side_effect=[first, RuntimeError('convert')]):
                with self.assertRaises(RuntimeError):
                    server.gpu_initializers('unused.onnx', 0, keep)
                self.assertEqual(keep[1:], [first])
                self.assertEqual(keep[0].value, allocated[0])
                self.assertEqual(freed, [] if failure == 'allocate' else allocated[1:])


if __name__ == '__main__':
    unittest.main()
