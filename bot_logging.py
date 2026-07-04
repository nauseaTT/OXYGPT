import logging as std_logging

std_logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=std_logging.DEBUG,
    handlers=[
        std_logging.FileHandler("telegramAI.log", encoding="utf-8"),
        std_logging.StreamHandler()
    ]
)
