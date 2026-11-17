"""Download pinned local retrieval weights. No application documents are uploaded."""
import json
from pathlib import Path
from huggingface_hub import snapshot_download
pins=json.loads(Path('work_assistant/retrieval/models.json').read_text())
for name,sha in pins.items():
    print('Preparing',name,sha,flush=True)
    snapshot_download(name,revision=sha,allow_patterns=['*.json','model.safetensors','*.txt','*.model'],ignore_patterns=['onnx/*','openvino/*'])
print('Pinned retrieval models ready.',flush=True)
