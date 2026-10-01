import torch

print("CUDA disponible:", torch.cuda.is_available())
if torch.cuda.is_available():
    print("GPU:", torch.cuda.get_device_name(0))
    print("VRAM total (GB):", torch.cuda.get_device_properties(0).total_memory / 1e9)
else:
    print("Version de torch:", torch.__version__)