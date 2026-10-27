import os
import logging

import pandas as pd

logger = logging.getLogger(__name__)

EXCEL_EXTENSIONS = {'.xlsx', '.xls'}
CSV_EXTENSIONS = {'.csv'}
TABULAR_EXTENSIONS = EXCEL_EXTENSIONS | CSV_EXTENSIONS


def parse_tabular_file(file_path: str) -> pd.DataFrame:
    """Read an Excel or CSV file into a cleaned DataFrame."""
    ext = os.path.splitext(file_path)[1].lower()
    logger.info(f"Parsing tabular file: {file_path} (ext={ext})")

    if ext in EXCEL_EXTENSIONS:
        df = _read_excel(file_path)
    elif ext in CSV_EXTENSIONS:
        df = _read_csv(file_path)
    else:
        raise ValueError(f"Unsupported tabular format: {ext}")

    df = _clean_dataframe(df)
    logger.info(f"Parsed {len(df)} rows, {len(df.columns)} columns from {os.path.basename(file_path)}")
    return df


def _read_excel(file_path: str) -> pd.DataFrame:
    try:
        df = pd.read_excel(file_path, engine="openpyxl")
    except Exception:
        df = pd.read_excel(file_path, engine="xlrd")
    return df


def _read_csv(file_path: str) -> pd.DataFrame:
    for encoding in ("utf-8", "cp1252", "latin-1"):
        try:
            return pd.read_csv(
                file_path,
                sep=None,
                engine="python",
                encoding=encoding,
                on_bad_lines="skip",
            )
        except UnicodeDecodeError:
            continue
        except Exception as e:
            logger.warning(f"CSV read failed with encoding {encoding}: {e}")
            continue
    raise ValueError("Could not read CSV with any supported encoding")


def _clean_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    df.columns = [str(c).strip() for c in df.columns]
    df = df.dropna(how="all")

    for col in df.select_dtypes(include=["object"]).columns:
        df[col] = df[col].astype(str).str.strip()
        df[col] = df[col].replace({"nan": "", "None": "", "NaT": ""})

    return df.reset_index(drop=True)


def dataframe_preview(df: pd.DataFrame, max_rows: int = 5) -> str:
    """Return column names + sample rows as a compact string for LLM prompts."""
    cols = list(df.columns)
    sample = df.head(max_rows).to_dict(orient="records")
    lines = [f"Columns: {cols}"]
    for i, row in enumerate(sample):
        lines.append(f"Row {i+1}: {row}")
    return "\n".join(lines)
