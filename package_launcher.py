"""PyInstaller entry point for the optional standalone Windows build."""
import threading
import webbrowser

from acc_engineer.__main__ import main


if __name__ == "__main__":
    threading.Timer(1.0, webbrowser.open, args=("http://localhost:8000",)).start()
    main()
