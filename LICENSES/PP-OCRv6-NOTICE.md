# PP-OCRv6 模型的来源与许可

flowocr 默认的 ONNX Runtime 推理路径使用 PaddlePaddle 官方发布的 PP-OCRv6 模型，由 `flowocr.models` 从 Hugging Face 按固定提交号下载：

| 用途 | 仓库 | 提交 | 许可 |
| --- | --- | --- | --- |
| 文字识别（rec） | [PaddlePaddle/PP-OCRv6_medium_rec_onnx](https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_rec_onnx) | `50c7eacafc52fa7bcf4194e8cd08e46f8558504b` | Apache License 2.0 |
| 文字检测（det） | [PaddlePaddle/PP-OCRv6_medium_det_onnx](https://huggingface.co/PaddlePaddle/PP-OCRv6_medium_det_onnx) | `61323801669c338b7891481ec7bac61ce31b576a` | Apache License 2.0 |

模型版权归 PaddlePaddle Authors（[PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR)）。
上游模型仓库未附 NOTICE 文件，也未附许可证全文；Apache License 2.0 全文在同目录的 `Apache-2.0.txt`
（取自 paddleocr 3.7.0 发行包的 `LICENSE`，含 PaddlePaddle Authors 的版权声明），亦见 <https://www.apache.org/licenses/LICENSE-2.0>。

## 我们做的修改

`inference_argmax.onnx` 由上游 `PP-OCRv6_medium_rec_onnx/inference.onnx` 派生：在计算图末尾追加 `ArgMax` 与 `ReduceMax` 两个节点，
并把图的输出从 logits 改为这两个节点的结果（`cls`、`prob`）。**权重与原有节点未作任何改动。**
实现见 `src/flowocr/models.py` 的 `fuse_argmax`。det 模型未作修改。
