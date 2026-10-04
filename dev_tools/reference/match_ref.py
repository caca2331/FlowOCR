import os
import re
import difflib
from dataclasses import dataclass
from typing import List, Tuple

# ==========================================
# 1. 核心配置区域 (User Configuration)
# ==========================================

# 算法参数
SIMILARITY_THRESHOLD = 0.5      # 基础相似度阈值
LAZY_TRUST_THRESHOLD = 0.5      # 如果局部匹配分数超过此值，直接采纳，不再进行全库搜索
GLOB_JUMP_THRESHOLD = 0.7       # 跳转的代价：全局分数有着更高的阈值
GLOB_CONF_THRESHOLD = 0.85      # 足够高的置信度以停止搜索


GAP_FILL_THRESHOLD = 1.0        # (秒) 两个字幕块间隔小于此值，则合并时间
TEXT_SPEED = 0.02               # (秒/字符) 游戏内文本显示速度, 用于对其字幕
LOOKAHEAD_NORMAL = 5            # (行) 常规搜索窗口
SHORT_PHRASE_LEN = 5            # (字符数) 短短语长度阈值, 用于相似度惩罚
context_sim_weight = 0.4        # 上下文加权比例

# debug信息
last_jump_idx = None
last_jump_msg = ""

# ==========================================
# 2. 数据结构
# ==========================================

@dataclass
class SubtitleItem:
    index: int
    start_seconds: float
    end_seconds: float
    text: str
    clean_text: str

@dataclass
class ReferenceItem:
    global_index: int       # 在整个合并列表中的索引
    source_file: str | None # 来源文件名
    
    jp_text: str
    cn_text: str
    clean_jp_text: str     # [新增] 预先清洗好的日文，用于快速比对
    display_delay: float   # (秒) 显示延迟，用于对齐字幕时间
    

# ==========================================
# 3. 文件处理与加载工具
# ==========================================

def parse_srt_time(time_str: str) -> float:
    try:
        parts = time_str.replace(',', ':').split(':')
        hours, minutes, seconds, milliseconds = map(int, parts)
        return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0
    except ValueError:
        return 0.0

def format_srt_time(seconds: float) -> str:
    millis = int((seconds - int(seconds)) * 1000)
    seconds = int(seconds)
    minutes = seconds // 60
    hours = minutes // 60
    minutes %= 60
    seconds %= 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"

def read_text_file(filepath: str) -> List[str]:
    """读取文件内容，按行分割，过滤空行"""
    lines = []
    try:
        # 尝试 UTF-8 读取，处理潜在的 BOM 或解码错误
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
            # 简单清洗：统一换行符
            content = content.replace('\r\n', '\n').replace('\r', '\n')
            raw_lines = content.split('\n')
            lines = [line.strip() for line in raw_lines if line.strip()]
    except Exception as e:
        print(f"[Warning] Failed to read {filepath}: {e}")
    return lines

def load_reference_directories(root: str, mode="all") -> List[ReferenceItem]:
    """
    递归遍历文件夹
    """
    file_count = 0
    combined_refs = []
    global_idx = 0

    # 获取所有日文文件的相对路径
    # 使用 sorted 确保跨平台顺序一致 (Linux/Windows)
    files = []
    for root, dirs, files in os.walk(root):
        # 排序文件夹和文件，保证顺序确定性
        dirs.sort() 
        files.sort() 
        for file in files:
            input_file_path = os.path.join(root, file)
            
            """ 后缀过滤器：只处理文本类文件 """
            valid_extensions = {'.txt', '.bytes'}
            ext = os.path.splitext(file)[1].lower()
            if ext not in valid_extensions:
                continue

            rel_path = os.path.relpath(input_file_path, root)

            try:
                with open(input_file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                jp_lines, cn_lines, line_types  = clean_and_extract(content)
                file_count += 1
            
            except UnicodeDecodeError:
                print(f"[错误] 文件编码非 UTF-8，已跳过: {rel_path}")
                continue
            except Exception as e:
                print(f"[错误] 处理 {rel_path} 失败: {e}")
                continue

            count = min(len(jp_lines), len(cn_lines))

            for i in range(count):
                # [优化] 在加载时就做完 Normalization
                raw_jp = jp_lines[i]
                cleaned_jp = normalize_text(raw_jp) 
                
                # 对话模式下，跳过非对话行
                if mode == "dialogue":
                    if line_types[i] in ["@choice", "@printDebate"]:
                        continue
                
                if mode == "other":
                    if line_types[i] not in ["@choice", "@printDebate"]:
                        continue
                    
                # Combine consecutive @choice lines
                if i > 0 and line_types[i] == line_types[i-1] == "@choice":
                    # 合并文本
                    combined_refs[-1].jp_text += "\\N" + raw_jp
                    combined_refs[-1].cn_text += "\\N" + cn_lines[i]
                    combined_refs[-1].clean_jp_text += cleaned_jp
                    continue
                
                if line_types[i] == "@choice":
                    display_delay = 0.1
                elif line_types[i] == "@printDebate":
                    display_delay = 0.8
                else:
                    display_delay = len(normalize_text(raw_jp, remove_punc=False)) * TEXT_SPEED + 0.1
                    
                combined_refs.append(ReferenceItem(
                    global_index=global_idx,
                    source_file=rel_path,
                    jp_text=raw_jp,
                    cn_text=cn_lines[i],
                    clean_jp_text=cleaned_jp,
                    display_delay = display_delay
                ))
                global_idx += 1

            # 填充分隔
            for _ in range(2):
                combined_refs.append(ReferenceItem(
                    global_index=global_idx,
                    source_file=None,
                    jp_text="",
                    cn_text="",
                    clean_jp_text="",
                    display_delay=0
                ))
                global_idx += 1 

    print(f"[Info] Total reference lines loaded: {len(combined_refs)}")
    return combined_refs

def load_srt(file_path: str) -> List[SubtitleItem]:
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    pattern = re.compile(r'(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n((?:(?!\n\n).)*)', re.DOTALL)
    matches = pattern.findall(content)
    
    subs = []
    for i, (idx, start, end, text) in enumerate(matches):
        text = text.replace('\n', '').strip()
        clean_text = normalize_text(text)
        if clean_text:
            subs.append(SubtitleItem(i, parse_srt_time(start), parse_srt_time(end), text, clean_text))
    return subs

# ==========================================
# 字符串清洗工具 (新增)
# ==========================================
KANA_NORMALIZATION_MAP = str.maketrans({
    # 平假名
    'ぁ': 'あ', 'ぃ': 'い', 'ぅ': 'う', 'ぇ': 'え', 'ぉ': 'お',
    'っ': 'つ',
    'ゃ': 'や', 'ゅ': 'ゆ', 'ょ': 'よ',
    'ゎ': 'わ',
    # 片假名 (OCR 也常把这些识别错)
    'ァ': 'ア', 'ィ': 'イ', 'ゥ': 'ウ', 'ェ': 'エ', 'ォ': 'オ',
    'ッ': 'ツ',
    'ャ': 'ヤ', 'ュ': 'ユ', 'ョ': 'ヨ',
    'ヮ': 'ワ',
    'ヵ': 'カ', 'ヶ': 'ケ'
})
def normalize_text(text: str, remove_punc=True) -> str:
    """
    清洗文本，去除所有标点符号、空格和特殊字符。
    只保留 汉字、假名、数字、字母。
    目的：解决 OCR 的 '...' 和 参考文本的 '……' 不匹配导致短句分数过低的问题。
    """
    # 步骤 1: 统一假名大小写 (使用高效的 translate)
    text = text.translate(KANA_NORMALIZATION_MAP)
    # 步骤 2: 去除ass样式标签
    text = re.sub(r'\\N', '', text)
    text = re.sub(r'\{\\.*?}', '', text)
    # 步骤 3: 去除标点 (原逻辑)
    # 匹配非单词字符 (包括标点、空格、制表符等)
    # 注意：\W 在 Python 3 中默认包含非 ASCII 字符，所以需要指定保留范围
    # 这里使用白名单模式：只保留 CJK 字符、假名、字母、数字
    # 简单粗暴版：直接把常见的标点符号洗掉
    if remove_punc:
        text = re.sub(r'[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', '', text)
    return text

# ==========================================
# 修改后的相似度计算
# ==========================================
# @lru_cache(maxsize=len())
def calculate_similarity(ref_clean: str, ocr_clean: str, penalize=True) -> float:
    # 归一化比对
    norm_score = 0
    if ocr_clean and ref_clean:
        # 剪枝逻辑
        norm_matcher = difflib.SequenceMatcher(None, ocr_clean, ref_clean)
        norm_score = norm_matcher.quick_ratio()
        if norm_score >= SIMILARITY_THRESHOLD / 2:
            norm_score = norm_matcher.ratio()
    # 对短短语的惩罚
    if penalize and len(ocr_clean) <= SHORT_PHRASE_LEN:
        penalty = (1 - len(ocr_clean) / SHORT_PHRASE_LEN) * 0.5
        norm_score = max(0.0, norm_score - penalty)
    return norm_score

# ==========================================
# 新增辅助函数: 范围搜索
# ==========================================
def find_best_match_in_range(
    ocr_subs: List[SubtitleItem], 
    current_ocr_idx: int,
    refs: List[ReferenceItem], 
    start_idx: int, end_idx: int, 
    penalize_short: bool = True,
    min_sim = SIMILARITY_THRESHOLD,
) -> Tuple[int, float]:
    
    best_idx = -1
    best_score = 0.0
    
    for i in range(max(0, start_idx), min(len(refs), end_idx)):
        ref_item = refs[i]

        # 1. 计算基础分数 (Base Score)
        score = calculate_similarity(
            ref_item.clean_jp_text, 
            ocr_subs[current_ocr_idx].clean_text, 
            penalize_short
        )
        
        # 2. 上下文链式校验 (Context Boost)
        # 只有当基础分数达到一定门槛有“苗头”时，才去浪费算力查后续
        # 给与local sim给与更高宽容度
        if score >= min_sim:
            bonus_accumulated = score
            
            # 往后预读 N 句
            for k in range(1, LOOKAHEAD_NORMAL + 1):
                # 边界检查: OCR 或 Ref 越界则停止
                next_ocr_idx = current_ocr_idx + k
                next_ref_idx = i + k
                
                if next_ocr_idx >= len(ocr_subs) or next_ref_idx >= len(refs):
                    break
                
                # 检测到分隔符
                if not refs[next_ref_idx].source_file:
                    break             

                # 临时计算后续句子的相似度
                next_score = calculate_similarity(
                    refs[next_ref_idx].clean_jp_text, 
                    ocr_subs[next_ocr_idx].clean_text,
                    penalize=penalize_short
                )
                
                # 如果后续句子也匹配上了，给予当前句子加分
                if next_score >= SIMILARITY_THRESHOLD:
                    # (a * (k-1) + b) / k = a + (b-a) / k
                    bonus_accumulated += (next_score - bonus_accumulated) / k
            
            # 加权分数 
            # score = score *0.4 + bonus_accumulated * 0.6
            score = max(score, bonus_accumulated) * (1-context_sim_weight) + min(score, bonus_accumulated) * context_sim_weight

        # 更新最佳匹配
        if score > best_score:
            best_score = score
            best_idx = i
            
        # 如果分数足够高，提前退出
        if score >= GLOB_CONF_THRESHOLD: break
            
    return best_idx, best_score

# ==========================================
# 修改后的核心处理逻辑
# ==========================================
def process_subtitles(ocr_subs: List[SubtitleItem], refs: List[ReferenceItem]):
    final_results = []
    ref_ptr = 0
    current_buffer = None 
    has_locked_on = False 
    
    total_ocr = len(ocr_subs)
    print(f"[Info] Processing {total_ocr} OCR lines (Dual-Track Search Strategy)...")

    for idx, ocr_item in enumerate(ocr_subs):
        if (idx + 1) % 500 == 0:
            print(f"[Progress] Processed {idx + 1}/{total_ocr} lines...")
        
        # ---------------------------------------------------
        # 步骤 1: 执行搜索 (双轨制)
        # ---------------------------------------------------
        
        # Track A: 局部搜索 (保持连贯性)
        # 只在已锁定且 ref_ptr 有效时执行
        local_idx, local_score = -1, 0.0
        if has_locked_on:
            local_search_end = ref_ptr + LOOKAHEAD_NORMAL + 5 # 往后看 5 + 5 行
            local_idx, local_score = find_best_match_in_range(
                ocr_subs=ocr_subs,       
                current_ocr_idx=idx,     
                refs=refs, 
                start_idx=ref_ptr, 
                end_idx=local_search_end,
                penalize_short=False,
                min_sim=0
            )

        # Track B: 全局搜索 (处理冷启动 & 剧情大跳跃)
        # 只有当: 1. 还没锁定 2. 或者局部匹配很烂(可能跳了) 
        global_idx, global_score = -1, 0.0
        
        # 性能权衡: 如果局部已经是完美匹配, 就不跑全库了，省点时间
        if local_score < LAZY_TRUST_THRESHOLD:
            global_idx, global_score = find_best_match_in_range(
                ocr_subs=ocr_subs,       
                current_ocr_idx=idx,     
                refs=refs, 
                start_idx=0, 
                end_idx=len(refs), 
            )

        # ---------------------------------------------------
        # 步骤 2: 裁判逻辑 (决定用哪个)
        # ---------------------------------------------------
        
        final_idx = -1
        final_score = 0.0
        
        if not has_locked_on:
            # [场景: 冷启动] 只信赖全局结果
            final_idx = global_idx
            final_score = global_score
        else:
            # [场景: 运行中] 比较 Local vs Global
            # 默认优先选 Local (稳定)，除非 Global 显著更强 (跳转)
            if global_score >= GLOB_JUMP_THRESHOLD:
                # 判定为: 剧情跳转 (Jump)
                final_idx = global_idx
                final_score = global_score

                # 快速跳转时print debug msg
                msg = f"[JUMP] @{idx+1} Ref# {ref_ptr}->{global_idx} Local:{local_score:.2f} vs Glob:{global_score:.2f} " \
                        f"{ocr_item.text[:15]} -> {refs[ref_ptr].jp_text[:10]} | {refs[global_idx].clean_jp_text[:10]}"
                global last_jump_idx, last_jump_msg
                if last_jump_idx and idx - last_jump_idx < 3 and final_score < GLOB_CONF_THRESHOLD:
                    if last_jump_msg: print(last_jump_msg)
                    print(msg)
                    last_jump_msg = ""    
                else:
                    last_jump_msg = msg
                last_jump_idx = idx
            else:
                # 判定为: 正常推进 (Sequential)
                final_idx = local_idx
                final_score = local_score

        # ---------------------------------------------------
        # 步骤 3: 提交与更新
        # ---------------------------------------------------
        
        is_match = final_score >= SIMILARITY_THRESHOLD
        
        if is_match:
            matched_ref = refs[final_idx]
            
            # 首次锁定提示
            if not has_locked_on:
                print(f"[LOCK] @{idx+1} Ref# {ref_ptr}->{global_idx} Local:{local_score:.2f} vs Glob:{global_score:.2f} "
                        f"{ocr_item.text[:15]} -> {refs[final_idx].clean_jp_text[:15]}")
                has_locked_on = True
                ref_ptr = final_idx # 初始化指针

            # Buffer 逻辑
            if current_buffer and current_buffer['ref_idx'] == final_idx:
                # 同一句的延伸
                current_buffer['end'] = max(current_buffer['end'], ocr_item.end_seconds)
            else:
                # 新的一句
                if current_buffer:
                    final_results.append(current_buffer)
                
                current_buffer = {
                    'ref_idx': final_idx,
                    'start': ocr_item.start_seconds - matched_ref.display_delay,
                    'end': ocr_item.end_seconds,
                    'jp_correct': matched_ref.jp_text,
                    'cn_text': matched_ref.cn_text,
                    'display_delay': matched_ref.display_delay
                }
                
                # 更新指针
                # 如果发生了跳转 (final_idx 变了)，ref_ptr 也就跟着变了
                # 只有当索引向前推进时才更新，防止偶尔匹配到前面的一句导致死循环(虽然概率很低)
                # 但如果是明确的 Jump (global wins)，我们允许指针回跳或大跳
                ref_ptr = final_idx 
        else:
            # 没匹配上
            # 如果原始字符较长, 且/或有一定的相似度, 打印debug信息
            if (local_score + 0.2 > LAZY_TRUST_THRESHOLD or global_score + 0.2 > GLOB_JUMP_THRESHOLD) and len(ocr_item.clean_text) > 2:
                msg = f"[SKIP] @{idx+1} Ref# {ref_ptr}->{global_idx} Local:{local_score:.2f} vs Glob:{global_score:.2f} " \
                        f"{ocr_item.text[:15]} -> {refs[ref_ptr].clean_jp_text[:10]} | {refs[global_idx].clean_jp_text[:10]}"
                print(msg)


    # 循环结束，提交最后一个 Buffer
    if current_buffer:
        final_results.append(current_buffer)

    return final_results

def post_process_gaps(results: List[dict]):
    count = 0
    for i in range(len(results) - 1):
        curr_item = results[i]
        next_item = results[i+1]
        if next_item['start'] - curr_item['end'] < 0:
            next_item["start"] = curr_item["end"]
        elif next_item['start'] - curr_item['end'] < GAP_FILL_THRESHOLD:
            curr_item['end'] = next_item['start']
            count += 1
    print(f"[Info] Post-process: Closed {count} gaps.")
    return results

# ==========================================
# 5. 导出功能
# ==========================================
def export_srt(results: List[dict], file_path: str, content_key: str):
    """
    导出 SRT
    :param content_key: 'jp_correct' 或 'cn_text'
    """
    with open(file_path, 'w', encoding='utf-8') as f:
        for i, item in enumerate(results):
            f.write(f"{i+1}\n")
            f.write(f"{format_srt_time(item['start'])} --> {format_srt_time(item['end'])}\n")
            # 写入指定的语言内容
            text_content = item[content_key].replace("<br>", "\n")
            f.write(f"{text_content}\n\n")
    print(f"[Success] Saved SRT: {file_path}")

def export_ass(results: List[dict], file_path: str, content_key: str):
    """
    导出 ASS
    :param content_key: 'jp_correct' 或 'cn_text'
    """
    ass_header = """[Script Info]
Title: Subtitle
ScriptType: v4.00+

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,20,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    
    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(ass_header)
        for item in results:
            start = format_ass_time(item['start'])
            end = format_ass_time(item['end'])
            text = item[content_key].replace("<br>", "\\N")
            f.write(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{text}\n")
    print(f"[Success] Saved ASS: {file_path}")

def format_ass_time(seconds: float) -> str:
    """Convert seconds to ASS time format (h:mm:ss.cc)"""
    millis = int((seconds - int(seconds)) * 100)
    seconds = int(seconds)
    minutes = seconds // 60
    hours = minutes // 60
    minutes %= 60
    seconds %= 60
    return f"{hours}:{minutes:02d}:{seconds:02d}.{millis:02d}"

""" 处理参考文本 """
def clean_and_extract(content):
    line_types = []
    jp_lines = []
    cn_lines = []
    
    # 替换 <link> 标签至ass样式
    # content = re.sub(r"<link=\".*?\">(.*?)</link>", r"{\\c&HAD84F1&\\fs70}\1{\\r}", content)
    content = re.sub(r"<link=\".*?\">(.*?)</link>", r"{\\c&HAD84F1&}\1{\\r}", content)

    # 替换 <b> 标签至ass样式
    content = re.sub(r"<b>(.*?)</b>", r"{\\b1}\1{\\b0}", content)

    # 替换 <color=#xxxxxx>...</color> 标签至ass样式
    content = re.sub(r'<color=#([0-9A-Fa-f]{6})>(.*?)</color>', 
                     lambda m: f"{{\\c&H{m.group(1)[4:6]}{m.group(1)[2:4]}{m.group(1)[0:2]}&}}{m.group(2)}{{\\r}}", 
                     content)
    
    # 替换 <size=xx>...</size> 标签至ass样式
    # content = re.sub(r'<size=(\d+)>(.*?)</size>', r'{\\fs\1}\2{\\r}', content)

    # 替换 <br> 为ass换行符
    content = re.sub(r'<br>', r'\\N', content)

    # 清除其他标签标签
    content = re.sub(r'<[^>]+>', '', content)

    # 清除 <ruby> 标签
    # 格式: <ruby="读音">文字</ruby>
    # content = re.sub(r'<ruby="[^"]+">([^<]+)</ruby>', r'\1', content)

    #  移除所有其他tag（除 <br> 外）
    # content = re.sub(r'<(?!br>)[^>]+>', '', content)
    
    # 3. 按行分割
    raw_lines = content.splitlines()

    line_type = "None"  # 当前行类型 对话 / 裁判 / 旁白 / 选项
    jp_raw = None
    cn_raw = ""

    def flush():
        nonlocal jp_raw, cn_raw, line_type
        if jp_raw and cn_raw:
            jp_raw = jp_raw.lstrip(';').strip()
            cn_raw = cn_raw.rstrip('\\N').strip()
            jp_lines.append(jp_raw)
            cn_lines.append(cn_raw)
            line_types.append(line_type)
            jp_raw = None
            cn_raw = ""
            line_type = "None"

    for line in raw_lines:
        line = line.strip()
        if not line: 
            pass
        elif line.startswith('#'):
            flush()
        elif line.startswith('; >'):
            line_type = line[3:].split()[0].strip(":")
        elif line.startswith(';'):
            jp_raw = line
        else:
            cn_raw += line

    flush()
    return jp_lines, cn_lines, line_types


def main(file_name, mode="all"):
    # 输入路径 (文件夹)
    INPUT_OCR_SRT = f"{file_name}.srt"     # OCR 生成的原始字幕文件
    REF_DIR = "./Text"                     # 参考文本根文件夹

    # 输出路径
    OUTPUT_JP_SRT = f"{file_name}_jp.ass"         # 修正后的日文 ass
    OUTPUT_CN_SRT = f"{file_name}_cn.ass"         # 对应的中文 ass

    # 1. 检查输入
    if not os.path.exists(REF_DIR):
        print("[Error] Reference directories not found.")
        print(f"Please ensure '{REF_DIR}' exist.")
        return

    try:
        # 2. 加载数据
        ocr_data = load_srt(INPUT_OCR_SRT)
        ref_data = load_reference_directories(REF_DIR, mode=mode)
        
        if not ref_data:
            print("[Error] No reference data loaded.")
            return

        # 3. 核心处理
        global context_sim_weight
        context_sim_weight = 0.4      # 默认对话模式权重
        if mode == "other":
            context_sim_weight = 0.2  # 非对话模式下，降低上下文权重
        processed_data = process_subtitles(ocr_data, ref_data)
        
        # 4. 后处理
        final_data = post_process_gaps(processed_data)

        # 5. 输出双语 SRT
        export_ass(final_data, OUTPUT_JP_SRT, 'jp_correct') # 日文修正版
        export_ass(final_data, OUTPUT_CN_SRT, 'cn_text')    # 中文版
        
    except Exception as e:
        print(f"[Fatal Error] {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    # for i in range(1, 6):
    #     file_name = f"lower{i}"
    #     main(file_name, "dialogue")
    #     file_name = f"full{i}"
    #     main(file_name, "other")
    main("meihong", "dialogue")
