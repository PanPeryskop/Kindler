import puremagic
from pathlib import Path

class FileTypeDetector:
    
    @staticmethod
    def detect_file_type(file_path: Path) -> tuple[str, bool]:
        file_extension = file_path.suffix.lower().replace(".", "")
        
        try:
            magic_results = puremagic.magic_file(str(file_path))
            
            if magic_results:
                extensions = [str(getattr(m, "extension", m)).lower().replace(".", "") for m in magic_results]
                
                if file_extension in extensions:
                    return file_extension, True
                    
                if file_extension in ["epub", "docx"] and "zip" in extensions:
                    return file_extension, True
                    
                real_ext = next((ext for ext in extensions if ext), "unknown")
                return real_ext, False
                
        except puremagic.PureError:
            if file_extension in ["txt", "html", "htm", "rtf", "mobi", "azw3", "azw"]:
                return file_extension, True
                
        return "unknown", False