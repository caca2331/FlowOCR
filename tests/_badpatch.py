"""守卫用：cluster_patch 给一个解析器不认的参数名——应当场报错，而不是静默失效。"""
def cluster_patch(context, options=None):
    return {'no_such_knob': 1}


def match(document, context, options=None):
    return {'schema': 'test/1', 'cues': []}
