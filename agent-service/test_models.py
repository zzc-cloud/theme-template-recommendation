import requests

key = "sk-qrumrpocxqhdbxywqpiibvsvgohruwvoktcywkjmuvoejtch"
models = [
    "Pro/zai-org/",
    "Pro/zai-org/",
    "THUDM/B-0414",
    "THUDM/",
    "THUDM/",
    "Pro/BAAI/bge-m3",
]
for model in models:
    try:
        resp = requests.post(
            "https://api.siliconflow.cn/v1/chat/completions",
            headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": "say hi in 3 words"}], "max_tokens": 20},
            timeout=15
        )
        print(f"{model}: {resp.status_code} - {resp.text[:200]}")
    except Exception as e:
        print(f"{model}: Error - {e}")
