"""Bounded artifact generation; callers never select filesystem paths."""

import csv
import io
import json
import re
from pathlib import Path
from uuid import uuid4
from xml.sax.saxutils import escape

from pydantic import BaseModel, ConfigDict, Field, field_validator

MIME = {
    'txt': 'text/plain', 'md': 'text/markdown', 'json': 'application/json',
    'csv': 'text/csv', 'tsv': 'text/tab-separated-values', 'html': 'text/html',
    'docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    'xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    'pptx': 'application/vnd.openxmlformats-officedocument.presentationml.presentation',
    'pdf': 'application/pdf',
}


class WriteArguments(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    filename: str = Field(min_length=1, max_length=120)
    content: str = Field(min_length=1, max_length=100_000)

    @field_validator('filename')
    @classmethod
    def safe_filename(cls, value):
        if (value in {'.', '..'} or any(c in value for c in '/\\:')
                or any(ord(c) < 32 for c in value) or value.startswith('.')
                or value.endswith((' ', '.')) or Path(value).suffix[1:].lower() not in MIME):
            raise ValueError('Use a plain filename with a supported extension')
        return value


class Artifact(BaseModel):
    id: str
    filename: str
    media_type: str
    size_bytes: int
    download_url: str


def render_file(filename: str, content: str) -> bytes:
    ext = Path(filename).suffix[1:].lower()
    if ext == 'json':
        json.loads(content)
    if ext in {'txt', 'md', 'json', 'csv', 'tsv', 'html'}:
        return content.encode('utf-8')
    output = io.BytesIO()
    if ext == 'docx':
        from docx import Document
        document = Document()
        for line in content.splitlines():
            heading = re.match(r'^(#{1,6}) (.*)', line)
            if heading:
                document.add_heading(heading[2], level=len(heading[1]))
            else:
                document.add_paragraph(line)
        document.save(output)
    elif ext == 'xlsx':
        from openpyxl import Workbook
        from openpyxl.styles import Font
        workbook = Workbook()
        sheet = workbook.active
        sheet.title = 'Data'
        for row_number, row in enumerate(csv.reader(io.StringIO(content)), 1):
            if len(row) > 100 or row_number > 5000:
                raise ValueError('Spreadsheet exceeds row or column limit')
            for column, value in enumerate(row, 1):
                cell = sheet.cell(row_number, column)
                cell.value = value
                cell.data_type = 's'  # Generated content is data, never executable formulas.
        for cell in sheet[1]:
            cell.font = Font(bold=True)
        sheet.freeze_panes = 'A2'
        workbook.save(output)
    elif ext == 'pptx':
        from pptx import Presentation
        deck = Presentation()
        sections = re.split(r'(?m)^---\s*$', content)
        if len(sections) > 50:
            raise ValueError('Maximum 50 slides')
        for section in sections:
            lines = section.strip().splitlines()
            if not lines:
                continue
            slide = deck.slides.add_slide(deck.slide_layouts[1])
            slide.shapes.title.text = lines[0].lstrip('# ')
            slide.placeholders[1].text = '\n'.join(lines[1:])
        deck.save(output)
    elif ext == 'pdf':
        from reportlab.pdfbase import pdfmetrics
        from reportlab.pdfbase.cidfonts import UnicodeCIDFont
        from reportlab.lib.styles import ParagraphStyle
        from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
        pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'))
        style = ParagraphStyle('body', fontName='STSong-Light', fontSize=11, leading=16, wordWrap='CJK')
        story = []
        for line in content.splitlines():
            if not line.strip():
                story.append(Spacer(1, 8))
                continue
            heading = re.match(r'^(#{1,6}) (.*)', line)
            if heading:
                heading_style = ParagraphStyle('heading', parent=style,
                    fontSize=18 if len(heading[1]) == 1 else 13, leading=22 if len(heading[1]) == 1 else 18,
                    spaceBefore=8, spaceAfter=6, keepWithNext=True)
                story.append(Paragraph(escape(heading[2]), heading_style))
            else:
                story.extend([Paragraph(escape(line), style), Spacer(1, 4)])
        SimpleDocTemplate(output).build(story)
    return output.getvalue()


class ArtifactStore:
    def __init__(self, root: Path):
        self.root = Path(root)

    def create(self, employee_id: str, args: WriteArguments, *, requires_database: bool = False) -> Artifact:
        from .telemetry import current_run
        context = current_run.get()
        data = render_file(args.filename, args.content)
        if len(data) > 5_000_000:
            raise ValueError('Generated file exceeds 5 MB')
        artifact_id = uuid4().hex
        artifact = Artifact(id=artifact_id, filename=args.filename,
                            media_type=MIME[Path(args.filename).suffix[1:].lower()],
                            size_bytes=len(data), download_url=f'/v1/files/{artifact_id}')
        folder = self.root / artifact_id
        folder.mkdir(parents=True, mode=0o700)
        try:
            (folder / 'content').write_bytes(data)
            (folder / 'metadata.json').write_text(json.dumps({
                'owner': employee_id, 'artifact': artifact.model_dump(),
                'requires_database': requires_database or context is not None}, ensure_ascii=False), encoding='utf-8')
        except OSError:
            for file in folder.iterdir():
                file.unlink()
            folder.rmdir()
            raise
        if context:
            database, run = context
            database.register_artifact(employee_id, artifact, run['id'])
        return artifact

    def read(self, employee_id: str, artifact_id: str, *, database=None) -> tuple[Artifact, Path]:
        if not re.fullmatch(r'[0-9a-f]{32}', artifact_id):
            raise LookupError('File not found')
        folder = self.root / artifact_id
        try:
            metadata = json.loads((folder / 'metadata.json').read_text(encoding='utf-8'))
            if metadata['owner'] != employee_id or not (folder / 'content').is_file():
                raise LookupError('File not found')
            if metadata.get('requires_database') and (database is None or not database.has_artifact(employee_id, artifact_id)):
                raise LookupError('File requires its originating database')
            return Artifact.model_validate(metadata['artifact']), folder / 'content'
        except FileNotFoundError:
            raise LookupError('File not found') from None
