from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator, model_validator
from .config import validate_base_url


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class KeyInput(Input):
    apiKey: str = Field(min_length=1, max_length=8192)
    verify: StrictBool = False

    @field_validator("apiKey")
    @classmethod
    def valid_key(cls, v):
        if v != v.strip() or any(ord(c) < 33 or ord(c) > 126 for c in v):
            raise ValueError("API 키에는 공백이나 제어 문자를 넣을 수 없습니다.")
        return v


class SettingsInput(Input):
    baseUrl: str | None = Field(default=None, max_length=2048)
    model: str | None = Field(default=None, min_length=1, max_length=256)
    systemPrompt: str | None = Field(default=None, max_length=8000)
    temperature: float | None = Field(default=None, ge=0, le=2, strict=True)
    topP: float | None = Field(default=None, ge=0, le=1, strict=True)
    maxTokens: int | None = Field(default=None, ge=1, le=65536, strict=True)
    thinking: StrictBool | None = None
    reasoningEffort: Literal["low", "high", "max"] | None = None
    searchProvider: Literal["auto", "searxng", "tavily", "brave"] | None = None
    webSearch: StrictBool | None = None

    @model_validator(mode="after")
    def no_null(self):
        if any(getattr(self, name) is None for name in self.model_fields_set):
            raise ValueError("설정 값에는 null을 넣을 수 없습니다.")
        return self

    @field_validator("baseUrl")
    @classmethod
    def base_allowed(cls, v):
        return validate_base_url(v) if v is not None else v

    @field_validator("model")
    @classmethod
    def nonblank(cls, v):
        if v is not None and not v.strip():
            raise ValueError("모델 이름을 입력해 주세요.")
        return v


class GenerationOptions(Input):
    reasoningEffort: Literal["low", "high", "max"] | None = None
    thinking: StrictBool | None = None
    webSearch: StrictBool | None = None

    @model_validator(mode="after")
    def no_null_options(self):
        if any(getattr(self, name) is None for name in self.model_fields_set
               if name in ("reasoningEffort", "thinking", "webSearch")):
            raise ValueError("추론 선택 값에는 null을 넣을 수 없습니다.")
        return self

    def overrides(self):
        return self.model_dump(include={"reasoningEffort", "thinking", "webSearch"}, exclude_unset=True)


class CreateConversation(GenerationOptions):
    title: str = Field(default="새 대화", min_length=1, max_length=120)


class RenameConversation(Input):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    starred: StrictBool | None = None

    @model_validator(mode="after")
    def valid_patch(self):
        if not self.model_fields_set or any(getattr(self, f) is None for f in self.model_fields_set):
            raise ValueError("변경할 제목 또는 즐겨찾기를 입력해 주세요.")
        return self


class MessageInput(GenerationOptions):
    content: str = Field(default="", max_length=32000)
    attachmentIds: list[UUID] | None = Field(default=None, max_length=5)

    @model_validator(mode="after")
    def valid_message(self):
        if "attachmentIds" in self.model_fields_set and self.attachmentIds is None:
            raise ValueError("첨부 목록은 배열이어야 합니다.")
        if self.attachmentIds and len(set(self.attachmentIds)) != len(self.attachmentIds):
            raise ValueError("첨부를 중복 선택할 수 없습니다.")
        if not self.content.strip() and not self.attachmentIds:
            raise ValueError("메시지를 입력해 주세요.")
        return self


class EditMessageInput(MessageInput):
    @model_validator(mode="after")
    def valid_message(self):
        if self.attachmentIds is None and "attachmentIds" not in self.model_fields_set:
            return self  # Existing attachments are checked inside the transaction.
        return super().valid_message()


class BulkDeleteInput(Input):
    ids: list[UUID] = Field(default_factory=list, max_length=1000)
    all: StrictBool = False
    keepStarred: StrictBool = False

    @model_validator(mode="after")
    def selection(self):
        if not self.all and not self.ids:
            raise ValueError("삭제할 대화를 선택해 주세요.")
        return self


class RetryInput(GenerationOptions):
    pass


class SearchKeysInput(Input):
    tavilyKey: str | None = Field(default=None, max_length=8192)
    braveKey: str | None = Field(default=None, max_length=8192)

    @model_validator(mode="after")
    def valid_keys(self):
        for name in self.model_fields_set:
            value = getattr(self, name)
            if value is None or any(ord(c) < 33 or ord(c) > 126 for c in value):
                raise ValueError("검색 키는 공백 없는 문자열이어야 합니다.")
        return self


class SearchTestInput(Input):
    provider: Literal["auto", "searxng", "tavily", "brave"] | None = None
