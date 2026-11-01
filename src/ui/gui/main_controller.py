"""Main window ViewPort write path: status / chat / music / emotion."""

from src.ui.gui.models import FaceModel, MainModel
from src.ui.gui.services import EmotionService


class MainWindowController:
    """Applies ViewPort set_* calls onto MainModel + FaceModel + EmotionService."""

    def __init__(
        self,
        main_model: MainModel | None = None,
        emotion_service: EmotionService | None = None,
        face_model: FaceModel | None = None,
    ) -> None:
        self._main_model = main_model or MainModel()
        self._emotion_service = emotion_service or EmotionService()
        self._face_model = face_model or FaceModel()

    @property
    def main_model(self) -> MainModel:
        return self._main_model

    @property
    def emotion_service(self) -> EmotionService:
        return self._emotion_service

    @property
    def face_model(self) -> FaceModel:
        return self._face_model

    def set_neutral_emotion(self) -> None:
        self.set_emotion("neutral")

    def set_chat_text(self, text: str) -> None:
        self._main_model.set_chat_text(text)

    def set_speech_text(self, role: str, text: str) -> None:
        self._face_model.set_speech_text(role, text)

    def end_speech(self) -> None:
        self._face_model.end_speech()

    def set_device_state(self, state: str) -> None:
        self._face_model.set_device_state(state)

    def set_music_line(self, text: str) -> None:
        self._main_model.set_music_line(text)
        self._face_model.interact()

    def set_emotion(self, emotion: str) -> None:
        url = self._emotion_service.get_emotion_url(emotion)
        self._main_model.set_emotion_url(url)
        self._face_model.set_emotion(self._emotion_service.normalize_emotion(emotion))

    def set_status(self, status: str, connected: bool = True) -> None:
        self._main_model.set_status(status, connected)

    def set_button_text(self, text: str) -> None:
        self._main_model.set_button_text(text)

    def set_auto_mode(self, auto_mode: bool) -> None:
        self._main_model.set_auto_mode(auto_mode)

    def is_auto_mode(self) -> bool:
        return bool(self._main_model._auto_mode)
