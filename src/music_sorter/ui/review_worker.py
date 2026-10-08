import threading

from PySide6.QtCore import QThread, Signal


class ReviewWorker(QThread):
    progress = Signal(int, int)
    result = Signal(object)
    error = Signal(str)

    def __init__(self, action, parent=None):
        super().__init__(parent)
        self.action = action
        self.cancel = threading.Event()

    def run(self):
        try:
            self.result.emit(self.action(self.cancel, self.progress.emit))
        except InterruptedError:
            self.result.emit({"cancelled": True})
        except ValueError as error:
            self.error.emit(str(error))
        except Exception as error:
            self.error.emit("작업을 마치지 못했습니다. 저장된 기록·폴더 접근 권한·저장 공간을 확인하세요.")
