"""
Build the FAISS vectorstore from the patient-language knowledge base.
Same architecture as Fertool — sentence-transformers + FAISS.

Usage:
  pip install sentence-transformers faiss-cpu langchain langchain-community
  python build_vectorstore.py

Output: ./education_vectorstore/ (FAISS index + metadata)
"""

import os
import re
import glob
from pathlib import Path

# Lazy imports with helpful error messages
try:
    from langchain.text_splitter import RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import FAISS
    from langchain_community.embeddings import HuggingFaceEmbeddings
except ImportError:
    print("Missing dependencies. Install with:")
    print("  pip install sentence-transformers faiss-cpu langchain langchain-community")
    exit(1)

KNOWLEDGE_DIR = "./knowledge"
OUTPUT_DIR = "./education_vectorstore"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Chunk size tuned for patient-education content:
# Larger than Fertool's because patient content is narrative,
# not bullet-point guidelines
CHUNK_SIZE = 800
CHUNK_OVERLAP = 150


def parse_knowledge_file(filepath: str) -> list[dict]:
    """Parse a knowledge markdown file into tagged chunks."""
    with open(filepath, "r") as f:
        content = f.read()

    documents = []
    filename = Path(filepath).stem

    # Extract stage tags
    stage_match = re.search(r'\[STAGE:\s*(.+?)\]', content)
    stages = [s.strip() for s in stage_match.group(1).split(",")] if stage_match else [filename]

    # Split by ## headings (education sections)
    sections = re.split(r'^## ', content, flags=re.MULTILINE)

    for section in sections[1:]:  # Skip the first (before any ##)
        lines = section.strip().split('\n')
        title = lines[0].strip()

        # Extract education tag if present
        edu_tag = None
        body_lines = []
        for line in lines[1:]:
            tag_match = re.match(r'\[EDUCATION:\s*(.+?)\]', line)
            if tag_match:
                edu_tag = tag_match.group(1)
            else:
                body_lines.append(line)

        body = '\n'.join(body_lines).strip()

        if not body:
            continue

        documents.append({
            "content": body,
            "metadata": {
                "title": title,
                "stages": stages,
                "education_tag": edu_tag or filename,
                "source_file": filename,
                "type": "patient_education",
            },
        })

    return documents


def build_vectorstore():
    """Build the FAISS vectorstore from all knowledge files."""
    print(f"Loading knowledge base from {KNOWLEDGE_DIR}/...")

    # Find all markdown files
    files = glob.glob(os.path.join(KNOWLEDGE_DIR, "*.md"))
    if not files:
        print(f"No .md files found in {KNOWLEDGE_DIR}/")
        return

    print(f"Found {len(files)} knowledge files")

    # Parse all files
    all_docs = []
    for filepath in sorted(files):
        docs = parse_knowledge_file(filepath)
        print(f"  {Path(filepath).name}: {len(docs)} sections")
        all_docs.extend(docs)

    print(f"\nTotal sections: {len(all_docs)}")

    # Split into chunks
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " "],
    )

    chunks = []
    metadatas = []
    for doc in all_docs:
        splits = splitter.split_text(doc["content"])
        for i, chunk in enumerate(splits):
            # Prepend title for better retrieval
            tagged_chunk = f"[EDUCATION] {doc['metadata']['title']}\n\n{chunk}"
            chunks.append(tagged_chunk)
            metadatas.append({
                **doc["metadata"],
                "chunk_index": i,
                "chunk_total": len(splits),
            })

    print(f"Total chunks after splitting: {len(chunks)}")

    # Build embeddings and FAISS index
    print(f"\nLoading embedding model: {EMBEDDING_MODEL}...")
    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": "cpu"},
    )

    print("Building FAISS index...")
    vectorstore = FAISS.from_texts(
        texts=chunks,
        embedding=embeddings,
        metadatas=metadatas,
    )

    # Save
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    vectorstore.save_local(OUTPUT_DIR)
    print(f"\nVectorstore saved to {OUTPUT_DIR}/")
    print(f"  {len(chunks)} chunks indexed")
    print(f"  Embedding dimension: {len(embeddings.embed_query('test'))}")

    # Quick test
    print("\n--- Test Query ---")
    results = vectorstore.similarity_search_with_score(
        "I'm scared about egg retrieval tomorrow",
        k=3,
    )
    for doc, score in results:
        relevance = 1 / (1 + score)
        print(f"  [{relevance:.2f}] {doc.metadata['title']} ({doc.metadata['source_file']})")
        print(f"         {doc.page_content[:100]}...")
        print()


if __name__ == "__main__":
    build_vectorstore()
