"""File mock. 알려진 파일 목록과 선택 이력을 유지한다.

select_file은 목록에 없는 이름도 성공으로 기록한다. 존재하지 않는 파일을
고른 행동은 툴 오류가 아니라 Agent의 State 오류로 평가하기 위해서다.
"""

from __future__ import annotations

from .types import ToolResult


class FileTool:
    """파일 이름에서 내용으로의 카탈로그."""

    def __init__(self, files: dict[str, str] | None = None) -> None:
        self._initial_files = dict(files or {})
        self.files = dict(self._initial_files)
        self.selected: list[str] = []
        self.deleted: list[str] = []

    def find_file(self, filename: str) -> ToolResult:
        """파일 이름이 카탈로그에 있는지 조회한다."""
        if not isinstance(filename, str) or not filename.strip():
            return ToolResult(False, "file", "find_file", {}, "filename must be a non-empty string")
        return ToolResult(
            True,
            "file",
            "find_file",
            {"filename": filename, "found": filename in self.files},
        )

    def select_file(self, filename: str) -> ToolResult:
        """Agent가 고른 파일 이름을 기록한다."""
        if not isinstance(filename, str) or not filename.strip():
            return ToolResult(False, "file", "select_file", {}, "filename must be a non-empty string")
        self.selected.append(filename)
        return ToolResult(
            True,
            "file",
            "select_file",
            {"filename": filename, "found": filename in self.files},
        )

    def delete_file(self, filename: str) -> ToolResult:
        """파일을 삭제한다. 삭제 금지 제약은 여기서 막지 않는다."""
        if not isinstance(filename, str) or not filename.strip():
            return ToolResult(False, "file", "delete_file", {}, "filename must be a non-empty string")
        found = filename in self.files
        self.files.pop(filename, None)
        self.deleted.append(filename)
        return ToolResult(True, "file", "delete_file", {"filename": filename, "found": found})

    def reset(self) -> None:
        """선택·삭제 이력을 비우고 처음 카탈로그로 되돌린다."""
        self.files = dict(self._initial_files)
        self.selected.clear()
        self.deleted.clear()
