import argparse
import os
import torch

def flatten_dict(d, parent_key='', sep='.'):
    """將嵌套字典展平，方便比較"""
    items = []
    if not isinstance(d, dict):
        return {parent_key: d} if parent_key else {"root": d}
        
    for k, v in d.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.extend(flatten_dict(v, new_key, sep=sep).items())
        else:
            items.append((new_key, v))
    return dict(items)

def analyze_tensor(tensor):
    """獲取張量統計資訊"""
    stats = {
        "形狀": list(tensor.shape),
        "資料型態": str(tensor.dtype),
        "參數數量": tensor.numel()
    }
    # 若為浮點數，計算基本的統計數值
    if tensor.numel() > 0 and tensor.dtype in [torch.float16, torch.float32, torch.float64, torch.bfloat16]:
        stats["最小值"] = tensor.min().item()
        stats["最大值"] = tensor.max().item()
        stats["平均值"] = tensor.float().mean().item()
    return stats

def analyze_single(file_path):
    """深度分析單一 .pth 檔案"""
    print(f"{'='*50}")
    print(f"深度分析單一檔案: {file_path}")
    print(f"{'='*50}")
    
    if not os.path.exists(file_path):
        print(f"錯誤: 找不到檔案 {file_path}")
        return
    
    try:
        # map_location='cpu' 避免在沒有 GPU 的環境報錯
        data = torch.load(file_path, map_location='cpu')
        print(f"成功載入檔案。根節點資料型態: {type(data).__name__}")
        
        flat_data = flatten_dict(data)
        print(f"展平後共包含 {len(flat_data)} 個節點。")
        
        total_params = 0
        tensor_count = 0
        
        for k, v in list(flat_data.items())[:50]: # 最多印出前 50 個
            print(f"\n[{k}]: ", end="")
            if isinstance(v, torch.Tensor):
                stats = analyze_tensor(v)
                print(f"張量 (Tensor)")
                for sk, sv in stats.items():
                    print(f"    - {sk}: {sv}")
                total_params += stats["參數數量"]
                tensor_count += 1
            else:
                print(f"{type(v).__name__}")
                print(f"    - 內容摘要: {str(v)[:100]}..." if len(str(v)) > 100 else f"    - 內容: {v}")
                
        if len(flat_data) > 50:
            print(f"\n... (省略其餘 {len(flat_data) - 50} 個節點) ...")
            
        print(f"\n--- 統計資訊 ---")
        print(f"總張量數量: {tensor_count}")
        print(f"總參數數量: {total_params:,}")

    except Exception as e:
        print(f"解析檔案時發生錯誤: {e}")

def compare_files(file_path1, file_path2):
    """比較兩個 .pth 檔案的差異"""
    print(f"{'='*50}")
    print(f"比較兩個檔案:")
    print(f"檔案 A: {file_path1}")
    print(f"檔案 B: {file_path2}")
    print(f"{'='*50}")
    
    if not os.path.exists(file_path1):
        print(f"錯誤: 找不到檔案 A ({file_path1})")
        return
    if not os.path.exists(file_path2):
        print(f"錯誤: 找不到檔案 B ({file_path2})")
        return

    try:
        data1 = torch.load(file_path1, map_location='cpu')
        data2 = torch.load(file_path2, map_location='cpu')
    except Exception as e:
        print(f"載入檔案時發生錯誤: {e}")
        return
        
    flat_data1 = flatten_dict(data1)
    flat_data2 = flatten_dict(data2)

    keys1 = set(flat_data1.keys())
    keys2 = set(flat_data2.keys())

    only_in_1 = keys1 - keys2
    only_in_2 = keys2 - keys1
    common_keys = keys1 & keys2

    print(f"\n[架構差異]")
    print(f"檔案 A 獨有的鍵 ({len(only_in_1)}):")
    for k in list(only_in_1)[:10]:
        print(f"  + {k}")
    if len(only_in_1) > 10: print("  ... (略)")

    print(f"\n檔案 B 獨有的鍵 ({len(only_in_2)}):")
    for k in list(only_in_2)[:10]:
        print(f"  + {k}")
    if len(only_in_2) > 10: print("  ... (略)")

    print(f"\n[數值與形狀差異 (針對共同鍵)]")
    diff_count = 0
    shape_diff_count = 0
    type_diff_count = 0
    exact_match_count = 0
    
    for k in common_keys:
        v1, v2 = flat_data1[k], flat_data2[k]
        
        if isinstance(v1, torch.Tensor) and isinstance(v2, torch.Tensor):
            if v1.shape != v2.shape:
                print(f"  ! [{k}] 形狀不同: A={list(v1.shape)}, B={list(v2.shape)}")
                shape_diff_count += 1
            else:
                if not torch.equal(v1, v2):
                    # 計算 L2 距離來衡量差異大小
                    diff_distance = torch.norm(v1.float() - v2.float()).item()
                    # print(f"  ! [{k}] 數值不同 (L2 差異: {diff_distance:.6f})")
                    diff_count += 1
                else:
                    exact_match_count += 1
        elif type(v1) != type(v2):
            print(f"  ! [{k}] 型態不同: A={type(v1).__name__}, B={type(v2).__name__}")
            type_diff_count += 1
        else:
            if v1 != v2:
                print(f"  ! [{k}] 內容不同 (非張量)")
                diff_count += 1
            else:
                exact_match_count += 1

    print(f"\n--- 比較總結 ---")
    print(f"共同鍵總數: {len(common_keys)}")
    print(f"完全一致的鍵數量: {exact_match_count}")
    print(f"形狀/維度不同的鍵數量: {shape_diff_count}")
    print(f"數值不同的鍵數量: {diff_count}")
    print(f"型態不同的鍵數量: {type_diff_count}")
    
    if shape_diff_count == 0 and diff_count == 0 and type_diff_count == 0 and len(only_in_1) == 0 and len(only_in_2) == 0:
        print("\n✅ 兩個檔案完全相同！")

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description="PyTorch .pth 檔案分析與比較工具")
    
    # 支援單一檔案輸入或兩個檔案輸入
    parser.add_argument('files', nargs='*', help='輸入一個或兩個 .pth 檔案路徑')
    
    # 也支援顯式的參數指定，以符合預設路徑的需求
    parser.add_argument('--file1', type=str, default='model/checkpoint_best_0312.pth', 
                        help='第一個 .pth 檔案路徑 (預設: model/checkpoint_best_0312.pth)')
    parser.add_argument('--file2', type=str, default='model/checkpoint_best.pth', 
                        help='第二個 .pth 檔案路徑 (預設: model/checkpoint_best.pth)')
    
    parser.add_argument('--mode', type=str, choices=['auto', 'analyze', 'compare'], default='auto',
                        help='執行模式: analyze (單一檔案), compare (全面比較), auto (自動判斷)。預設為 auto')
    
    args = parser.parse_args()
    
    # 決定執行模式
    mode = args.mode
    target_file1 = args.file1
    target_file2 = args.file2
    
    if len(args.files) == 1:
        target_file1 = args.files[0]
        if mode == 'auto':
            mode = 'analyze'
    elif len(args.files) >= 2:
        target_file1 = args.files[0]
        target_file2 = args.files[1]
        if mode == 'auto':
            mode = 'compare'
    
    if mode == 'auto':
        mode = 'compare' # 預設提供兩個預設路徑的比較
        
    # 進入對應的處理邏輯
    if mode == 'analyze':
        analyze_single(target_file1)
    elif mode == 'compare':
        compare_files(target_file1, target_file2)
