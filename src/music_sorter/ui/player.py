from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QPainter, QPixmap
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QSlider, QVBoxLayout
from .design import icon


class Player(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("playerBar")
        self.path = None
        self.player = QMediaPlayer(self)
        self.audio = QAudioOutput(self)
        self.audio.setVolume(0.5)
        self.player.setAudioOutput(self.audio)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(18, 12, 18, 12)
        layout.setSpacing(16)
        self.cover = QLabel()
        self.cover.setFixedSize(52, 52)
        self.fallback_cover()
        layout.addWidget(self.cover)
        labels = QVBoxLayout()
        labels.setSpacing(3)
        self.label = QLabel("음악을 선택하세요")
        self.label.setMaximumWidth(260)
        self.label.setMinimumWidth(140)
        self.label.setStyleSheet("font-weight: 600;")
        self.artist = QLabel("선택한 곡을 여기서 재생할 수 있어요")
        self.artist.setObjectName("subtle")
        self.artist.setMaximumWidth(260)
        labels.addWidget(self.label)
        labels.addWidget(self.artist)
        layout.addLayout(labels)
        self.play_button = QPushButton()
        self.play_button.setObjectName("playButton")
        self.play_button.setFixedSize(48, 48)
        self.play_button.setIcon(icon("play", "#173F32"))
        self.play_button.setToolTip("재생 / 일시정지")
        self.play_button.setAccessibleName("재생 또는 일시정지")
        self.play_button.clicked.connect(self.toggle)
        self.play_button.setEnabled(False)
        layout.addWidget(self.play_button)
        timeline = QVBoxLayout()
        timeline.setSpacing(5)
        time_row = QHBoxLayout()
        self.elapsed = QLabel("0:00")
        self.elapsed.setObjectName("subtle")
        self.duration = QLabel("0:00")
        self.duration.setObjectName("subtle")
        time_row.addWidget(self.elapsed)
        time_row.addStretch()
        time_row.addWidget(self.duration)
        timeline.addLayout(time_row)
        self.seek = QSlider(Qt.Orientation.Horizontal)
        self.seek.setAccessibleName("재생 위치")
        self.seek.sliderReleased.connect(lambda: self.player.setPosition(self.seek.value()))
        self.player.durationChanged.connect(self.duration_changed)
        self.player.positionChanged.connect(self.position_changed)
        timeline.addWidget(self.seek)
        layout.addLayout(timeline, 1)
        volume_row = QVBoxLayout()
        volume_title = QLabel("볼륨")
        volume_title.setObjectName("subtle")
        volume_row.addWidget(volume_title)
        self.volume = QSlider(Qt.Orientation.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(50)
        self.volume.setFixedWidth(90)
        self.volume.setAccessibleName("재생 볼륨")
        self.volume.valueChanged.connect(lambda value: self.audio.setVolume(value / 100))
        volume_row.addWidget(self.volume)
        layout.addLayout(volume_row)
        external = QPushButton("기본 음악 앱")
        external.setObjectName("ghost")
        external.clicked.connect(self.open_external)
        layout.addWidget(external)
        self.player.playbackStateChanged.connect(lambda state: self.play_button.setIcon(icon(
            "pause" if state == QMediaPlayer.PlaybackState.PlayingState else "play", "#173F32")))
        self.player.errorOccurred.connect(lambda *_: self.label.setText("재생 실패 · 기본 음악 앱으로 열어 확인하세요"))
        self._pending_position = None
        self.player.mediaStatusChanged.connect(self._seek_loaded)

    @staticmethod
    def time_label(value):
        seconds = max(0, int(value) // 1000)
        return f"{seconds // 60}:{seconds % 60:02d}"

    def duration_changed(self, value):
        self.seek.setRange(0, int(value))
        self.duration.setText(self.time_label(value))

    def fallback_cover(self):
        cover = QPixmap(104, 104)
        cover.fill(QColor("#203C36"))
        painter = QPainter(cover)
        painter.drawPixmap(24, 24, icon("music", "#8ADABD", 56).pixmap(56, 56))
        painter.end()
        self.cover.setPixmap(cover.scaled(52, 52, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

    def load_cover(self):
        self.fallback_cover()
        try:
            from mutagen.id3 import ID3
            tags = ID3(self.path)
            for image in tags.getall("APIC"):
                if len(image.data) > 2_000_000:
                    continue
                cover = QPixmap()
                if cover.loadFromData(image.data):
                    self.cover.setPixmap(cover.scaled(52, 52, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                                      Qt.TransformationMode.SmoothTransformation))
                    break
        except Exception:
            pass

    def set_track(self, path: str, retain_position=False, title="", artist=""):
        position = self.player.position() if retain_position else 0
        playing = self.player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        self.stop()
        self.path = Path(path)
        self.label.setText(title or self.path.stem)
        self.label.setToolTip(title or self.path.name)
        self.artist.setText(artist or "아티스트 정보 없음")
        self.load_cover()
        self.play_button.setEnabled(True)
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
        self.elapsed.setText(self.time_label(value))
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
        self.label.setText("음악을 선택하세요")
        self.artist.setText("선택한 곡을 여기서 재생할 수 있어요")
        self.play_button.setEnabled(False)
        self.fallback_cover()
