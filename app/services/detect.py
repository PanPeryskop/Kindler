from pathlib import Path

import puremagic

ZIP_BASED = ("epub", "docx")
UNSIGNED_TEXT = ("txt", "html", "htm", "rtf", "mobi", "azw3", "azw")


class FileTypeDetector:
    @staticmethod
    def detect_file_type(file_path: Path) -> tuple[str, bool]:
        file_extension = file_path.suffix.lower().replace(".", "")

        try:
            magic_results = puremagic.magic_file(str(file_path))
        except puremagic.PureError:
            if file_extension in UNSIGNED_TEXT:
                return file_extension, True
            return "unknown", False

        if not magic_results:
            return "unknown", False

        extensions = [
            str(getattr(match, "extension", match)).lower().replace(".", "")
            for match in magic_results
        ]
        if file_extension in extensions:
            return file_extension, True
        if file_extension in ZIP_BASED and "zip" in extensions:
            return file_extension, True
        return next((ext for ext in extensions if ext), "unknown"), False
