from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout, QWidget


class Player(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.path = None
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.5)
        self.player.setAudioOutput(self.audio)
        layout = QVBoxLayout(self)
        self.label = QLabel("선택한 곡을 재생할 수 있습니다")
        self.label.setWordWrap(True)
        layout.addWidget(self.label)
        controls = QHBoxLayout()
        self.play_button = QPushButton("▶ 재생 / 일시정지")
        self.play_button.clicked.connect(self.toggle)
        controls.addWidget(self.play_button)
        external = QPushButton("기본 음악 앱")
        external.clicked.connect(self.open_external)
        controls.addWidget(external)
        layout.addLayout(controls)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("재생 위치")
        self.seek.sliderReleased.connect(lambda: self.player.setPosition(self.seek.value()))
        self.player.durationChanged.connect(lambda value: self.seek.setRange(0, int(value)))
        self.player.positionChanged.connect(self.position_changed)
        layout.addWidget(self.seek)
        volume_row = QHBoxLayout()
        volume_row.addWidget(QLabel("볼륨"))
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(50)
        self.volume.setAccessibleName("재생 볼륨")
        self.volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        volume_row.addWidget(self.volume)
        layout.addLayout(volume_row)
        self.player.errorOccurred.connect(lambda *_: self.label.setText("재생 실패 · 기본 음악 앱으로 열어 확인하세요"))
        self._pending_position = None
        self.player.mediaStatusChanged.connect(self._seek_loaded)

    def set_track(self, path: str, retain_position=False):
        position = self.player.position() if retain_position else 0
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.stop()
        self.path = Path(path)
        self.label.setText(self.path.name)
        self._pending_position = position if retain_position else None
        self.player.setSource(QUrl.fromLocalFile(str(self.path)))
        if retain_position:
            if playing:
                self.player.play()

    def _seek_loaded(self, status):
        if self._pending_position is not None and status in {QMediaPlayer.MediaStatus.LoadedMedia, QMediaPlayer.MediaStatus.BufferedMedia}:
            self.player.setPosition(min(self._pending_position, self.player.duration()))
            self._pending_position = None

    def position_changed(self, value):
        if not self.seek.isSliderDown():
            self.seek.setValue(int(value))

    def toggle(self):
        if not self.path:
            return
        if self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def open_external(self):
        if self.path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.path)))

    def stop(self):
        self.player.stop()
        self.player.setSource(QUrl())
        self.path = None
        self._pending_position = None
        self.label.setText("선택한 곡을 재생할 수 있습니다")
