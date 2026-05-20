import asyncio
import sys

import uvicorn


if sys.platform.startswith("win"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


if __name__ == "__main__":
    # log_config=None 讓 uvicorn 繼承 main.py 的 configure_logging() 設定，
    # 避免 uvicorn 用自己的文字格式覆蓋 JSON formatter。
    uvicorn.run("main:app", host="127.0.0.1", port=8200, loop="asyncio", log_config=None)
