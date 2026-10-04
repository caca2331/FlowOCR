import os
import re
import json
import difflib
from dataclasses import dataclass
from typing import List, Tuple

'''
锚点: 上一次最后匹配的位置
lazy search: search上一次最后匹配的位置 (in case 重复)和其下一条
有没有锚点 有->lazy search; 无->global search
如果lazy search: lazy search的分数数高于lazy search阈值: 采纳; 否则进行global search
global search过程中: 分数分数高于global search阈值: 采纳, 否则继续
global search结束后: 如果最高分低于global search 最低要求阈值: 跳过这一条.
打分逻辑:
query: 当前查询的srt行; msg: 当前要打分的数据行. query+1指query的下一行.
pre_score = mean(sim(query-1, msg-1) + sim(query-2, msg-2) + sim(query-3 ,msg-3))
post_score =mean(sim(query+1, msg+1) + sim(query+2, msg+2) + sim(query+3, msg+3)
score = sim(query, msg)
a,b,c = sorted(pre_score, score, post_score)
overall_score = 0.2*score + 0.5*c + 0.3*b + 0.2*a
sim: 基于difflib的ratio, 但对于短文本(<5 chars)进行惩罚, 惩罚方式为: penalty = (1-len/5)*0.5; 最终得分 = max(0, score - penalty)
'''

# ==========================================
# 1. Configuration
# ==========================================
LAZY_ACCEPT_THRESHOLD = 0.6           # lazy search 采纳阈值
GLOBAL_ACCEPT_THRESHOLD = 0.9         # global search 提前采纳阈值
GLOBAL_MIN_THRESHOLD = 0.5            # global search 完成后的最低要求

GAP_FILL_THRESHOLD = 1.0          # seconds
TEXT_SPEED = 0.02                 # seconds per char, used for a tiny lead time
SHORT_PHRASE_LEN = 5              # chars

last_jump_idx = None
last_jump_msg = ""


# ==========================================
# 2. Data structures
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
    global_index: int
    character_jp: str
    character_kn: str
    jp_lines: List[str]
    kn_lines: List[str]
    combined_jp_text: str        # character + jp lines (multiline)
    combined_kn_text: str        # translated name + kn lines (multiline)
    clean_with_char: str         # normalized jp text including character
    clean_no_char: str           # normalized jp text without character
    display_delay: float


# ==========================================
# 3. Helpers
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


KANA_NORMALIZATION_MAP = str.maketrans({
    'ぁ': 'あ', 'ぃ': 'い', 'ぅ': 'う', 'ぇ': 'え', 'ぉ': 'お',
    'っ': 'つ',
    'ゃ': 'や', 'ゅ': 'ゆ', 'ょ': 'よ',
    'ゎ': 'わ',
    'ァ': 'ア', 'ィ': 'イ', 'ゥ': 'ウ', 'ェ': 'エ', 'ォ': 'オ',
    'ッ': 'ツ',
    'ャ': 'ヤ', 'ュ': 'ユ', 'ョ': 'ヨ',
    'ヮ': 'ワ',
    'ヵ': 'カ', 'ヶ': 'ケ'
})


def normalize_text(text: str, remove_punc: bool = True) -> str:
    text = text.translate(KANA_NORMALIZATION_MAP)
    text = re.sub(r'\\N', '', text)
    text = re.sub(r'\{\\.*?}', '', text)
    if remove_punc:
        text = re.sub(r'[^\w\u4e00-\u9fff\u3040-\u309f\u30a0-\u30ff]', '', text)
    return text


# ==========================================
# 4. Loaders
# ==========================================

def load_srt(file_path: str) -> List[SubtitleItem]:
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    pattern = re.compile(
        r'(\d+)\s*\n'
        r'(\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2},\d{3})\s*\n'
        r'(.*?)\n(?=\s*\d+\s*\n|\s*$)',
        re.DOTALL
    )

    subs = []
    for i, (idx, start, end, text) in enumerate(pattern.findall(content)):
        raw_text = text.rstrip()
        clean_text = normalize_text(raw_text.replace('\n', ''))
        if clean_text:
            subs.append(SubtitleItem(i, parse_srt_time(start), parse_srt_time(end), raw_text, clean_text))
    return subs


def load_reference_from_json(json_path: str) -> List[ReferenceItem]:
    with open(json_path, 'r', encoding='utf-8') as f:
        data = json.load(f)

    # build character name map for translation
    char_map = {}
    for ch in data.get('character', []):
        jp_name = (ch.get('jp') or '').strip()
        kn_name = (ch.get('kn') or '').strip()
        char_map[jp_name] = kn_name
        char_map[ch.get('id', '').replace('name_', '')] = kn_name

    refs: List[ReferenceItem] = []
    global_idx = 0

    for conv in data.get('conversation', []):
        character_jp = (conv.get('character') or '').strip()
        character_kn = char_map.get(character_jp, character_jp)

        jp_lines = [line.strip() for line in conv.get('jp', []) if line]
        kn_lines = [line.strip() for line in conv.get('kn', []) if line]

        # build display text (multiline, preserving original newlines)
        jp_parts = []
        kn_parts = []
        if character_jp:
            jp_parts.append(character_jp)
            kn_parts.append(character_kn)
        jp_parts.extend(jp_lines)
        kn_parts.extend(kn_lines)

        combined_jp_text = "\n".join(jp_parts)
        combined_kn_text = "\n".join(kn_parts)

        clean_no_char = normalize_text("".join(jp_lines))
        clean_with_char = normalize_text(combined_jp_text)

        display_delay = len(normalize_text("".join(jp_lines), remove_punc=False)) * TEXT_SPEED + 0.1

        refs.append(ReferenceItem(
            global_index=global_idx,
            character_jp=character_jp,
            character_kn=character_kn,
            jp_lines=jp_lines,
            kn_lines=kn_lines,
            combined_jp_text=combined_jp_text,
            combined_kn_text=combined_kn_text,
            clean_with_char=clean_with_char,
            clean_no_char=clean_no_char,
            display_delay=display_delay,
        ))
        global_idx += 1

    print(f"[Info] Total reference lines loaded: {len(refs)}")
    return refs


# ==========================================
# 5. Similarity
# ==========================================

def calculate_similarity(ref_clean: str, ocr_clean: str, penalize: bool = True) -> float:
    score = 0.0
    if ocr_clean and ref_clean:
        matcher = difflib.SequenceMatcher(None, ocr_clean, ref_clean)
        score = matcher.quick_ratio()
        if score >= GLOBAL_MIN_THRESHOLD / 2:
            score = matcher.ratio()

    if penalize and len(ocr_clean) <= SHORT_PHRASE_LEN:
        penalty = (1 - len(ocr_clean) / SHORT_PHRASE_LEN) * 0.5
        score = max(0.0, score - penalty)
    return score


def best_similarity(ref: ReferenceItem, ocr_clean: str, penalize: bool) -> float:
    # speaker name is optional; take the better of with/without
    return max(
        calculate_similarity(ref.clean_with_char, ocr_clean, penalize),
        calculate_similarity(ref.clean_no_char, ocr_clean, penalize)
    )


def similarity_for_pair(
    ocr_idx: int,
    ref_idx: int,
    ocr_subs: List[SubtitleItem],
    refs: List[ReferenceItem],
    cache: dict,
) -> float:
    if ocr_idx < 0 or ref_idx < 0 or ocr_idx >= len(ocr_subs) or ref_idx >= len(refs):
        return 0.0

    key = (ocr_idx, ref_idx)
    if key in cache:
        return cache[key]

    score = best_similarity(refs[ref_idx], ocr_subs[ocr_idx].clean_text, True)
    cache[key] = score
    return score


def contextual_score(
    ocr_idx: int,
    ref_idx: int,
    ocr_subs: List[SubtitleItem],
    refs: List[ReferenceItem],
    cache: dict,
) -> float:
    score = similarity_for_pair(ocr_idx, ref_idx, ocr_subs, refs, cache)

    # Fast path: very low base score, skip extra context work
    if score < 0.2:
        return score

    pre_list = [similarity_for_pair(ocr_idx - k, ref_idx - k, ocr_subs, refs, cache) for k in range(1, 4)]
    pre_list = [s for s in pre_list if s > 0]
    pre_score = sum(pre_list) / len(pre_list) if pre_list else 0.0

    post_list = [similarity_for_pair(ocr_idx + k, ref_idx + k, ocr_subs, refs, cache) for k in range(1, 4)]
    post_list = [s for s in post_list if s > 0]
    post_score = sum(post_list) / len(post_list) if post_list else 0.0

    a, b, c = sorted([pre_score, score, post_score])
    overall = 0.2 * score + 0.5 * c + 0.3 * b + 0.2 * a
    return overall


def find_best_match_in_range(
    ocr_subs: List[SubtitleItem],
    current_ocr_idx: int,
    refs: List[ReferenceItem],
    start_idx: int,
    end_idx: int,
    accept_threshold: float,
    min_threshold: float,
    cache: dict,
) -> Tuple[int, float]:

    best_idx = -1
    best_score = 0.0

    for i in range(max(0, start_idx), min(len(refs), end_idx)):
        score = contextual_score(current_ocr_idx, i, ocr_subs, refs, cache)

        if score > best_score:
            best_score = score
            best_idx = i

        if score >= accept_threshold:
            best_idx = i
            best_score = score
            break

    if best_score < min_threshold:
        return -1, best_score

    return best_idx, best_score


# ==========================================
# 6. Core alignment logic
# ==========================================

def process_subtitles(ocr_subs: List[SubtitleItem], refs: List[ReferenceItem]):
    final_results = []
    anchor_idx = None  # 上一次匹配到的 ref 索引
    current_buffer = None

    total_ocr = len(ocr_subs)
    print(f"[Info] Processing {total_ocr} OCR lines (Lazy + Global Search)...")

    for idx, ocr_item in enumerate(ocr_subs):
        if (idx + 1) % 500 == 0:
            print(f"[Progress] Processed {idx + 1}/{total_ocr} lines...")

        final_idx = -1
        final_score = 0.0
        cache = {}  # cache similarity_for_pair within this OCR line's searches
        global_idx, global_score = -1, 0.0
        lazy_hit = False

        # 1) Lazy search：仅检查锚点和其下一条
        if anchor_idx is not None:
            lazy_idx, lazy_score = find_best_match_in_range(
                ocr_subs=ocr_subs,
                current_ocr_idx=idx,
                refs=refs,
                start_idx=anchor_idx,
                end_idx=anchor_idx + 2,
                accept_threshold=LAZY_ACCEPT_THRESHOLD,
                min_threshold=0.0,
                cache=cache,
            )
        else:
            lazy_idx, lazy_score = -1, 0.0

        if lazy_idx != -1 and lazy_score >= LAZY_ACCEPT_THRESHOLD:
            final_idx, final_score = lazy_idx, lazy_score
            lazy_hit = True
        else:
            # 2) Global search
            global_idx, global_score = find_best_match_in_range(
                ocr_subs=ocr_subs,
                current_ocr_idx=idx,
                refs=refs,
                start_idx=0,
                end_idx=len(refs),
                accept_threshold=GLOBAL_ACCEPT_THRESHOLD,
                min_threshold=GLOBAL_MIN_THRESHOLD,
                cache=cache,
            )
            final_idx, final_score = global_idx, global_score

        # 快速跳转 debug：lazy 失效且 global 命中
        if anchor_idx is not None and not lazy_hit and final_idx != -1:
            msg = (
                f"[JUMP] @{idx+1} Ref# {anchor_idx}->{global_idx} "
                f"Lazy:{lazy_score:.2f} vs Glob:{global_score:.2f} "
                f"{ocr_item.text.splitlines()[0][:15]} -> {refs[global_idx].combined_jp_text.splitlines()[0][:15]}"
            )
            global last_jump_idx, last_jump_msg
            if last_jump_idx is not None and idx - last_jump_idx < 3 and final_score < GLOBAL_ACCEPT_THRESHOLD:
                if last_jump_msg:
                    print(last_jump_msg)
                print(msg)
                last_jump_msg = ""
            else:
                last_jump_msg = msg
            last_jump_idx = idx

        # 3) 采纳 or 跳过
        if final_idx == -1 or final_score < GLOBAL_MIN_THRESHOLD:
            if (lazy_score + 0.2 > LAZY_ACCEPT_THRESHOLD or global_score + 0.2 > GLOBAL_ACCEPT_THRESHOLD) and len(ocr_item.clean_text) > 2:
                print(
                    f"[SKIP] @{idx+1} Ref# {anchor_idx}->{global_idx} Lazy:{lazy_score:.2f} vs Glob:{global_score:.2f} "
                    f"{ocr_item.text.splitlines()[0][:15]}"
                )
            continue

        matched_ref = refs[final_idx]

        # 记录锚点
        anchor_idx = final_idx

        if current_buffer and current_buffer['ref_idx'] == final_idx:
            current_buffer['end'] = max(current_buffer['end'], ocr_item.end_seconds)
        else:
            if current_buffer:
                final_results.append(current_buffer)

            current_buffer = {
                'ref_idx': final_idx,
                'start': ocr_item.start_seconds - matched_ref.display_delay,
                'end': ocr_item.end_seconds,
                'matched_jp_text': matched_ref.combined_jp_text,
                'matched_kn_text': matched_ref.combined_kn_text,
                'display_delay': matched_ref.display_delay,
            }

    if current_buffer:
        final_results.append(current_buffer)

    return final_results


def post_process_gaps(results: List[dict]):
    count = 0
    for i in range(len(results) - 1):
        curr = results[i]
        nxt = results[i + 1]
        if nxt['start'] - curr['end'] < 0:
            nxt['start'] = curr['end']
        elif nxt['start'] - curr['end'] < GAP_FILL_THRESHOLD:
            curr['end'] = nxt['start']
            count += 1
    print(f"[Info] Post-process: Closed {count} gaps.")
    return results


# ==========================================
# 7. Export
# ==========================================

def export_srt(results: List[dict], file_path: str, content_key: str = 'matched_jp_text'):
    with open(file_path, 'w', encoding='utf-8') as f:
        for i, item in enumerate(results):
            f.write(f"{i + 1}\n")
            f.write(f"{format_srt_time(item['start'])} --> {format_srt_time(item['end'])}\n")
            text_content = item[content_key]
            f.write(f"{text_content}\n\n")
    print(f"[Success] Saved SRT: {file_path}")


# ==========================================
# 8. Entrypoint
# ==========================================

def process_file(input_path: str, refs: List[ReferenceItem], output_dir: str):
    base = os.path.splitext(os.path.basename(input_path))[0]
    output_path_jp = os.path.join(output_dir, f"{base}_jp.srt")
    output_path_kn = os.path.join(output_dir, f"{base}_kn.srt")

    ocr_data = load_srt(input_path)
    if not ocr_data:
        print(f"[Warning] No OCR lines found in {input_path}")
        return

    processed = process_subtitles(ocr_data, refs)
    final_data = post_process_gaps(processed)
    export_srt(final_data, output_path_jp, 'matched_jp_text')
    export_srt(final_data, output_path_kn, 'matched_kn_text')


def main():
    INPUT_DIR = './input'
    REF_JSON = './parsedText.json'
    OUTPUT_DIR = './output'

    if not os.path.exists(REF_JSON):
        print(f"[Error] Reference json not found: {REF_JSON}")
        return

    os.makedirs(OUTPUT_DIR, exist_ok=True)

    refs = load_reference_from_json(REF_JSON)
    if not refs:
        print('[Error] No reference data loaded.')
        return

    srt_files = [f for f in os.listdir(INPUT_DIR) if f.lower().endswith('.srt')]
    if not srt_files:
        print(f"[Error] No srt files found in {INPUT_DIR}")
        return

    for fname in sorted(srt_files):
        process_file(os.path.join(INPUT_DIR, fname), refs, OUTPUT_DIR)


if __name__ == '__main__':
    main()
