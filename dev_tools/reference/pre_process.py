import os
import re
import shutil
import argparse
import sys

def clean_and_extract(content):
    """ 处理单个文件内容的逻辑核心 """
    line_types = []
    jp_lines = []
    cn_lines = []
    
    # ============================================================
    # [修复] 预处理 <br> 换行
    # 原来的逻辑: r'(<br>)\s*[\r\n]+' 会吞掉分隔 Block 的空行，导致 Header 被合并
    # 新的逻辑: 只有当 <br> 后的下一行内容 不是 以 # 开头时，才合并换行
    # (?!\s*#) 是负向先行断言，意思是“后面不能紧跟(空白+#)”
    # ============================================================
    content = re.sub(r'(<br>)[ \t]*[\r\n]+(?!\s*#)', r'\1', content)
    
    # 替换 <link> 标签至ass样式
    content = re.sub(r"<link=\".*?\">(.*?)</link>", r"{\\c&HAD84F1&\\fs70}\1{\\r}", content)

    # 替换 <b> 标签至ass样式
    content = re.sub(r"<b>(.*?)</b>", r"{\\b1}\1{\\b0}", content)

    # 替换 <color=#xxxxxx>...</color> 标签至ass样式
    content = re.sub(r'<color=#([0-9A-Fa-f]{6})>(.*?)</color>', 
                     lambda m: f"{{\\c&H{m.group(1)[4:6]}{m.group(1)[2:4]}{m.group(1)[0:2]}&}}{m.group(2)}{{\\r}}", 
                     content)
    
    # 替换 <size=xx>...</size> 标签至ass样式
    content = re.sub(r'<size=(\d+)>(.*?)</size>', r'{\\fs\1}\2{\\r}', content)

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
    
    # # 4. 预处理: 移除以 "; >" 开头的指令注释行
    # filtered_lines = [line for line in raw_lines if not line.strip().startswith('; >')]

    
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
    return jp_lines, cn_lines   

def process_directory(source_dir):
    source_path = os.path.abspath(source_dir)
    parent_dir = os.path.dirname(source_path)
    base_name = os.path.basename(source_path)
    
    if not base_name:
        print("错误: 无法处理根目录或路径格式不正确。")
        return

    output_jp_root = os.path.join(parent_dir, base_name + "jp")
    output_cn_root = os.path.join(parent_dir, base_name + "cn")

    print(f"源目录: {source_path}")
    print(f"预定输出 (JP): {output_jp_root}")
    print(f"预定输出 (CN): {output_cn_root}")
    

    file_count = 0
    skipped_count = 0

    for root, dirs, files in os.walk(source_path):
        for file in files:
            input_file_path = os.path.join(root, file)
            
            """ 后缀过滤器：只处理文本类文件 """
            valid_extensions = {'.txt', '.bytes'}
            ext = os.path.splitext(file)[1].lower()
            if ext not in valid_extensions:
                continue

            rel_path = os.path.relpath(input_file_path, source_path)
            output_jp_path = os.path.join(output_jp_root, rel_path)
            output_cn_path = os.path.join(output_cn_root, rel_path)

            os.makedirs(os.path.dirname(output_jp_path), exist_ok=True)
            os.makedirs(os.path.dirname(output_cn_path), exist_ok=True)

            try:
                with open(input_file_path, 'r', encoding='utf-8') as f:
                    content = f.read()

                jp_data, cn_data = clean_and_extract(content)

                if jp_data:
                    with open(output_jp_path, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(jp_data))
                
                if cn_data:
                    with open(output_cn_path, 'w', encoding='utf-8') as f:
                        f.write('\n'.join(cn_data))
                
                print(f"处理完成: {rel_path}")
                file_count += 1

            except UnicodeDecodeError:
                print(f"[错误] 文件编码非 UTF-8，已跳过: {rel_path}")
                skipped_count += 1
            except Exception as e:
                print(f"[错误] 处理 {rel_path} 失败: {e}")
                skipped_count += 1

    print("-" * 30)
    print(f"全部完成。成功处理: {file_count} 个，跳过/失败: {skipped_count} 个。")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="安全批量提取文本脚本")
    parser.add_argument("path", type=str, help="输入文件夹路径")
    args = parser.parse_args()

    if os.path.exists(args.path):
        process_directory(args.path)
    else:
        print(f"错误: 找不到路径 '{args.path}'")
