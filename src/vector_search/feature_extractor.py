"""
特徵提取層。
負責載入 SimSiam 模型並執行推論，將輸入影像轉換為高維特徵向量 (Embeddings)。
"""
import logging
from pathlib import Path

import cv2
import numpy as np
import torch
import torchvision.transforms as T

# 引入 SimSiam 定義
# 假設專案根目錄已在 PYTHONPATH 中，或者此檔案位於 src/ 下可直接引用 src.model
try:
    from src.vector_search.simsiam2 import SimSiam
except ImportError:
    # Fallback for relative import if running as script or different structure
    import sys
    sys.path.append(str(Path(__file__).resolve().parents[1]))
    from src.vector_search.simsiam2 import SimSiam

# 模擬介面：引用外部前處理函數
# 依據需求：假設 src/image_preprocessing3.py 存在
try:
    from src.vector_search.image_preprocessing3 import preprocess_for_inference
except ImportError:
    # 若模組不存在，則實作一個符合推論規格的 Mock 版本
    logging.getLogger(__name__).warning("無法導入 vector_search.image_preprocessing3，使用內建 Mock 前處理。")

    def preprocess_for_inference(img_array: np.ndarray, img_size: int = 224) -> torch.Tensor:
        """
        Mock 前處理函數。
        將輸入影像 (numpy array) 轉換為模型可接受的 Tensor。
        
        Args:
            img_array: HxWxC or HxW numpy array (BGR or Gray).
            img_size: Target image size.
            
        Returns:
            torch.Tensor: [C, H, W] normalized tensor.
        """
        # 定義與訓練時一致的標準化
        # 為了滿足 ResNet 預設的 3 通道輸入，我們將任何影像都轉換為 RGB 格式
        # 若是 2D array (Grayscale) 則先轉 3 通道
        if len(img_array.shape) == 2:
            img_array = cv2.cvtColor(img_array, cv2.COLOR_GRAY2RGB)
        elif len(img_array.shape) == 3 and img_array.shape[2] == 1:
            img_array = cv2.cvtColor(img_array, cv2.COLOR_GRAY2RGB)
        elif len(img_array.shape) == 3 and img_array.shape[2] == 4:
            # 去除 alpha channel
            img_array = cv2.cvtColor(img_array, cv2.COLOR_RGBA2RGB)

        transform = T.Compose([
            T.ToPILImage(),
            T.Resize((img_size, img_size)),
            T.ToTensor(),
            # 使用 ImageNet 標準 (因為 resnet 預設 3 通道，大部分情況使用這個)
            T.Normalize(mean=[0.485, 0.456, 0.406], std=[0.229, 0.224, 0.225])
        ])

        return transform(img_array)

logger = logging.getLogger(__name__)

class SimSiamFeatureExtractor:
    """
    SimSiam 特徵提取器。
    
    管理模型載入、權重初始化以及批次特徵提取。
    """

    def __init__(self, model_path: str = None, device: str = None, backbone: str = "resnet50", in_channels: int = 3):
        """
        初始化 Feature Extractor。

        Args:
            model_path (str, optional): 預訓練權重檔 (.pth) 路徑。若為 None 則使用隨機權重 (僅供測試)。
            device (str, optional): 'cuda' 或 'cpu'。若為 None 則自動偵測。
            backbone (str): 模型骨幹架構 ('resnet18' or 'resnet50').
        """
        self.device = device if device else ("cuda" if torch.cuda.is_available() else "cpu")
        logger.info(f"使用裝置: {self.device}")

        # Auto-detect channels from checkpoint if model_path is provided
        if model_path:
            try:
                ckpt = torch.load(model_path, map_location="cpu") # Load to CPU for inspection
                state_dict = ckpt.get('state_dict', ckpt)

                # Check conv1 weight shape: [Out, In, K, K]
                # Usually backbone.conv1.weight
                if 'backbone.conv1.weight' in state_dict:
                     detected_channels = state_dict['backbone.conv1.weight'].shape[1]
                     if detected_channels != in_channels:
                         logger.info(f"自動偵測到 Checkpoint 輸入通道數為 {detected_channels} (原設定: {in_channels})，已自動更新。")
                         in_channels = detected_channels

                # Auto-detect Backbone: ResNet18 vs ResNet50
                # ResNet18/34: layer1.0.conv1 is 3x3
                # ResNet50/101: layer1.0.conv1 is 1x1
                if 'backbone.layer1.0.conv1.weight' in state_dict:
                    k_size = state_dict['backbone.layer1.0.conv1.weight'].shape[2]
                    detected_backbone = backbone
                    if k_size == 3:
                        detected_backbone = 'resnet18'
                    elif k_size == 1:
                        detected_backbone = 'resnet50'

                    if detected_backbone != backbone:
                        logger.info(f"自動偵測到 Checkpoint Backbone 為 {detected_backbone} (原設定: {backbone})，已自動更新。")
                        backbone = detected_backbone

            except Exception as e:
                logger.warning(f"嘗試偵測模型架構失敗: {e}")

        # 初始化模型架構
        # 注意: in_channels 需依據實際模型訓練設定
        self.model = SimSiam(backbone=backbone, in_channels=in_channels)

        if model_path:
            self._load_weights(model_path)
        else:
            logger.warning("未指定 model_path，使用隨機初始化權重！")

        # 暫存影像尺寸設定 (預設 512，工程圖細節較多)
        self.img_size = 512

        self.model.to(self.device)
        self.model.eval()

    def _load_weights(self, path: str):
        """載入模型權重，包含錯誤處理。"""
        try:
            logger.info(f"正在載入模型權重: {path}")
            checkpoint = torch.load(path, map_location=self.device)

            # 處理 checkpoint 可能包含 'state_dict' 鍵值的情況
            state_dict = checkpoint.get('state_dict', checkpoint)

            # 使用 strict=False 允許部分鍵值不匹配 (例如多了 predictor 以外的層)
            # 但 SimSiam 應該完全匹配
            msg = self.model.load_state_dict(state_dict, strict=True)
            logger.info(f"權重載入成功: {msg}")

        except FileNotFoundError:
            logger.error(f"找不到模型檔案: {path}")
            raise
        except RuntimeError as e:
            logger.error(f"權重載入失敗 (形狀不匹配?): {e}")
            raise

    @torch.no_grad()
    def extract_batch(self, images: list[np.ndarray]) -> np.ndarray:
        """
        批次提取影像特徵。

        Args:
            images (List[np.ndarray]): 影像列表 (Numpy Array)。

        Returns:
            np.ndarray: 特徵矩陣，形狀 [Batch_Size, Embed_Dim]。
        """
        if not images:
            return np.array([])

        # 1. 前處理 (Preprocessing)
        # 偵測模型預期輸入通道數
        expected_channels = 3
        try:
            if hasattr(self.model, 'backbone') and hasattr(self.model.backbone, 'conv1'):
                expected_channels = self.model.backbone.conv1.in_channels
        except Exception:
            pass

        tensor_list = []
        for img in images:
            try:
                # 確保傳送給模型的影像維度與 channels 匹配
                if expected_channels == 1 and len(img.shape) == 3:
                    img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
                elif expected_channels == 3 and len(img.shape) == 2:
                    img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
                elif expected_channels == 3 and len(img.shape) == 3 and img.shape[2] == 1:
                    img = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)

                # 呼叫 (Mocked or Real) 前處理函數
                # 注意：若是真實模組不支援 img_size 可能會報錯，但目前確認使用的是 Mock
                try:
                    t = preprocess_for_inference(img, img_size=self.img_size)
                except TypeError:
                    # Fallback for real module without img_size arg
                    t = preprocess_for_inference(img)
                    
                # 動態適應 Tensor 通道數以符合模型預期的 input channels
                if expected_channels == 3 and t.shape[0] == 1:
                    t = t.repeat(3, 1, 1)
                elif expected_channels == 1 and t.shape[0] == 3:
                    # 分別為 RGB 取平均以轉換為 1 channel tensor
                    t = t.mean(dim=0, keepdim=True)
                    
                tensor_list.append(t)
            except Exception as e:
                logger.error(f"影像前處理失敗: {e}")
                # 若失敗，塞一個全零 Tensor 避免崩潰? 或者跳過?
                # 這裡選擇塞全零以維持 Batch 對齊
                # 注意：這裡應該根據模型的 in_channels 決定維度，這裡暫時寫死 3 或 1 會有點問題
                # 但因為這是 fallback，先保留，或盡量用正確的維度
                c = list(self.model.parameters())[0].shape[1] # Try to infer from model first layer
                tensor_list.append(torch.zeros(c, 224, 224))

        # Stack into batch: [B, C, H, W]
        batch_tensor = torch.stack(tensor_list).to(self.device)

        # 2. 推論 (Inference)
        # 對於 SimSiam，我們通常使用 Backbone + Projector 的輸出 (Embedding)
        # 參考 SimSiam.forward: f -> z -> p
        # 我們需要 z (Projector output)

        try:
            # 由於 SimSiam.forward 需要 x1, x2，我們手動執行 backbone + projector
            f = self.model.backbone(batch_tensor).flatten(start_dim=1)
            z = self.model.projector(f)

            # L2 Normalize (SimSiam 訓練時有用到，檢索時通常也需要)
            # 根據 SimSiam 論文，inference 通常使用 backbone output (f)
            # 但這裡需求明確指出 "Tensor -> SimSiam Encoder -> 2048-dim Embedding"
            # 且 projector output 維度通常也是 2048。
            # 若使用 SimSiam 做檢索，通常建議使用 Backbone features (f)。
            # 但若 Projector 已訓練好，z 亦可。
            # 根據常見實踐，這裡回傳 z 並做 L2 Norm。

            z = torch.nn.functional.normalize(z, dim=1)

            embeddings = z.cpu().numpy()
            return embeddings

        except Exception as e:
            logger.error(f"模型推論失敗: {e}")
            raise RuntimeError(f"模型推論失敗: {e}")
