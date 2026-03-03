class Colors:
    """终端颜色控制"""
    GREEN = '\033[92m'
    RED = '\033[91m'
    YELLOW = '\033[93m'
    RESET = '\033[0m'

    @staticmethod
    def green(text: str) -> str:
        return f"{Colors.GREEN}{text}{Colors.RESET}"

    @staticmethod
    def red(text: str) -> str:
        return f"{Colors.RED}{text}{Colors.RESET}"

    @staticmethod
    def yellow(text: str) -> str:
        return f"{Colors.YELLOW}{text}{Colors.RESET}"