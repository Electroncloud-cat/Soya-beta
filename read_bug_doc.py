import zipfile
import xml.etree.ElementTree as ET

def read_docx(file_path):
    """Read text from a .docx file without python-docx"""
    try:
        with zipfile.ZipFile(file_path, 'r') as zip_ref:
            xml_content = zip_ref.read('word/document.xml')
            tree = ET.fromstring(xml_content)

            # Extract all text from the XML
            namespaces = {
                'w': 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'
            }

            paragraphs = []
            for paragraph in tree.findall('.//w:p', namespaces):
                texts = []
                for text in paragraph.findall('.//w:t', namespaces):
                    if text.text:
                        texts.append(text.text)
                if texts:
                    paragraphs.append(''.join(texts))

            return '\n'.join(paragraphs)
    except Exception as e:
        return f"Error reading document: {e}"

if __name__ == "__main__":
    content = read_docx(r'D:\pythonproject\Soya_Agent\soya-v0.2.2\Soya-beta_BugMix.docx')
    with open('bug_list.txt', 'w', encoding='utf-8') as f:
        f.write(content)
    print("Bug document content saved to bug_list.txt")
