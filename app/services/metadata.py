import ebooklib
from ebooklib import epub
from pathlib import Path
from slugify import slugify

class EpubMetadataExtractorandRenamer:
    
    @staticmethod
    def extract_and_rename_epub(epub_path: Path) -> tuple[str, str, str]:
        try:
            book = epub.read_epub(str(epub_path))
            
            titles = book.get_metadata('DC', 'title')
            authors = book.get_metadata('DC', 'creator')
            
            title = titles[0][0] if titles else "Nieznany_Tytul"
            author = authors[0][0] if authors else "Nieznany_Autor"
            
            clean_title = slugify(title, separator='_')
            clean_author = slugify(author, separator='_')
            
            new_filename = f"{clean_author}-{clean_title}.epub"
            
            return new_filename, title, author
            
        except Exception as e:
            fallback_name = f"{slugify(epub_path.stem)}.epub"
            return fallback_name, epub_path.stem, "Unknown"
        