import struct

def analyze_corrupted_zip(filepath):
    print(f"分析損壞的 ZIP 檔案: {filepath}")
    with open(filepath, 'rb') as f:
        data = f.read()

    # ZIP local file header signature: 50 4B 03 04
    signature = b'\x50\x4B\x03\x04'
    offset = 0
    found_files = []
    
    while True:
        offset = data.find(signature, offset)
        if offset == -1:
            break
            
        try:
            # Local file header structure:
            # 0: 4 bytes - signature
            # 4: 2 bytes - version needed to extract
            # 6: 2 bytes - general purpose bit flag
            # 8: 2 bytes - compression method
            # 10: 2 bytes - last mod file time
            # 12: 2 bytes - last mod file date
            # 14: 4 bytes - crc-32
            # 18: 4 bytes - compressed size
            # 22: 4 bytes - uncompressed size
            # 26: 2 bytes - file name length (n)
            # 28: 2 bytes - extra field length (m)
            # 30: n bytes - file name
            
            header = data[offset:offset+30]
            if len(header) < 30:
                break
                
            _, _, _, comp_method, _, _, _, comp_size, uncomp_size, name_len, extra_len = struct.unpack('<IHHHHHIIIHH', header)
            
            filename = data[offset+30 : offset+30+name_len].decode('utf-8', errors='ignore')
            found_files.append({
                'name': filename,
                'offset': offset,
                'comp_size': comp_size,
                'uncomp_size': uncomp_size
            })
            
            # Move past this header
            offset += 30 + name_len + extra_len + comp_size
        except Exception as e:
            offset += 4 # Move past signature if parsing fails
            
    print(f"共找到 {len(found_files)} 個內部檔案片段。")
    if found_files:
        print("前 20 個檔案:")
        for f in found_files[:20]:
            print(f"  - {f['name']} (大小: {f['comp_size']} bytes)")
        if len(found_files) > 20:
            print("  ...")
            print("最後 5 個檔案 (可能是斷點處):")
            for f in found_files[-5:]:
                print(f"  - {f['name']} (大小: {f['comp_size']} bytes)")

if __name__ == "__main__":
    analyze_corrupted_zip("model/checkpoint_best_0512.pth")
