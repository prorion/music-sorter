from PySide6.QtCore import QThread

from ..connections import ConnectionError, list_models


class ConnectionWorker(QThread):
    def __init__(self, provider, vault, workspace_id="", parent=None):
        super().__init__(parent)
        self.provider, self.vault, self.workspace_id = provider, vault, workspace_id
        self.outcome = None
        self.message = ""

    def run(self):
        try:
            key = self.vault.get(self.provider)
        except Exception:
            self.message = "저장소 접근 오류 · Windows 자격 증명 저장소를 확인하세요."
            return
        try:
            self.outcome = list_models(self.provider, key or "", self.workspace_id)
        except ConnectionError as error:
            self.message = str(error)
        except Exception:
            self.message = "연결 확인 실패 · 키 입력 형식과 네트워크 설정을 확인하세요."
