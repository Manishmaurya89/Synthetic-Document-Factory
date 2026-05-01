# Synthetic Document Factory 🏭

An AI-powered pipeline to generate professional-grade, multi-page synthetic documents in multiple languages (English, Hindi, Urdu, Telugu, Spanish, French, German).

## 🚀 Quick Start (Docker)

The easiest way to run the factory is using Docker.

1. **Clone the repository:**
   ```bash
   git clone https://github.com/yourusername/synthetic-doc-factory.git
   cd synthetic-doc-factory
   ```

2. **Set up Environment Variables:**
   Copy the example file and add your own API keys. **NEVER commit your `.env` file to GitHub.**
   ```bash
   cp .env.example .env
   ```
   Edit `.env` and add your:
   - `UNSPLASH_ACCESS_KEY` (for images)
   - `OLLAMA_URL` (usually `http://host.docker.internal:11434` if running Ollama on host)

3. **Run with Docker Compose:**
   ```bash
   docker-compose up --build
   ```

4. **Access the UI:**
   Open [http://localhost:5001](http://localhost:5001) in your browser.

## 🛠 Features
- **Multilingual Support:** Strict language ratio enforcement for 7 languages.
- **Visuals:** Auto-generated tables and high-fidelity image fetching.
- **Smart Script Detection:** Renders Devanagari, Arabic, and Telugu scripts correctly in PDFs.
- **Web Dashboard:** Interactive UI to configure and generate documents.

## 🔒 Security & API Keys
This project uses a `.env` file for sensitive keys. 
- The `.gitignore` file is configured to **automatically ignore** `.env`.
- Always use `.env.example` to share the structure without the actual data.
- For production/cloud deployment, use GitHub Secrets or environment variables in your CI/CD pipeline.

## 📄 License
MIT
