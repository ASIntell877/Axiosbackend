import os
import json

# === Config ===
RAW_PATH = r"C:\Maximos\data\raw"
CHUNK_OUTPUT_PATH = r"C:\Maximos\data\chunks"

def load_text(file_path):
    with open(file_path, 'r', encoding='utf-8') as f:
        return f.read()

def split_into_chunks(text, max_words=350):
    paragraphs = text.split('\n')
    chunks = []
    chunk = ""
    word_count = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue

        words = para.split()
        if word_count + len(words) > max_words:
            chunks.append(chunk.strip())
            chunk = para + "\n"
            word_count = len(words)
        else:
            chunk += para + "\n"
            word_count += len(words)

    if chunk:
        chunks.append(chunk.strip())

    return chunks

def process_file(filename, title):
    file_path = os.path.join(RAW_PATH, filename)
    print(f"Processing: {file_path}")

    text = load_text(file_path)
    chunks = split_into_chunks(text)

    data = []
    for i, chunk in enumerate(chunks):
        data.append({
            "source": title,
            "chunk_id": i,
            "text": chunk
        })

    output_filename = f"{title.replace(' ', '_').lower()}.json"
    output_path = os.path.join(CHUNK_OUTPUT_PATH, output_filename)
    
    os.makedirs(CHUNK_OUTPUT_PATH, exist_ok=True)

    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    print(f"Saved {len(chunks)} chunks to: {output_path}")

# === Process your actual files ===
process_file("lettertomarinus.txt", "Letter to Marinus on the Filioque")


