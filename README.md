<div align="center">

#  Synthetic Document Factory

### AI-Powered Multilingual Document Generation Pipeline

[![Python](https://img.shields.io/badge/python-3.9+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Docker](https://img.shields.io/badge/docker-enabled-2496ED.svg?logo=docker)](https://www.docker.com/)
[![Flask](https://img.shields.io/badge/flask-2.0+-000000.svg?logo=flask)](https://flask.palletsprojects.com/)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](http://makeapullrequest.com)

[Features](#-features) • [Quick Start](#-quick-start) • [Documentation](#-documentation) • [Contributing](#-contributing) • [License](#-license)

---

</div>

## 📖 Overview

**Synthetic Document Factory** is an advanced AI-powered pipeline designed to generate professional-grade, multi-page synthetic documents in multiple languages and scripts. Perfect for creating training datasets for OCR systems, document processing AI, and multilingual NLP applications.

### Why Synthetic Document Factory?

- **🌍 True Multilingual Support**: Generate documents in 7 languages with proper script rendering
- **🎨 Professional Quality**: Auto-generated tables, high-quality images, and realistic formatting
- **🚀 Production Ready**: Containerized deployment with Docker, web dashboard included
- **🔧 Highly Configurable**: Customize templates, fonts, layouts, and content ratios
- **🤖 AI-Powered**: Leverages local LLMs via Ollama for intelligent content generation

---

##  Features

### Core Capabilities

| Feature | Description |
|---------|-------------|
| **Multi-Language Support** | English, Hindi, Urdu, Telugu, Spanish, French, German |
| **Script Rendering** | Native support for Latin, Devanagari, Arabic, and Telugu scripts |
| **Visual Elements** | Auto-generated tables, charts, and high-fidelity image integration |
| **Smart Layout** | Professional formatting with configurable templates |
| **Batch Generation** | Generate multiple documents in parallel |
| **Web Interface** | Interactive dashboard for configuration and generation |

### Technical Highlights

-  **Pipeline Architecture**: Modular stages for content generation, assembly, and rendering
-  **Language Ratio Enforcement**: Precise control over multilingual content distribution
-  **Image Integration**: Unsplash API integration for contextual, high-quality images
-  **Data Visualization**: Automatic table and chart generation
-  **Font Management**: Embedded fonts for accurate multi-script PDF rendering
-  **Security First**: Environment-based configuration, no hardcoded secrets

---

## 🚀 Quick Start

### Prerequisites

Before you begin, ensure you have the following installed:

- **Docker & Docker Compose** (recommended) - [Install Docker](https://docs.docker.com/get-docker/)
- **Python 3.9+** (for non-Docker setup)
- **Ollama** - [Install Ollama](https://ollama.ai/) (for AI content generation)

### Option 1: Docker Setup (Recommended)

The fastest way to get started:

```bash
# 1. Clone the repository
git clone https://github.com/Manishmaurya89/Synthetic-Document-Factory.git
cd Synthetic-Document-Factory

# 2. Set up environment variables
cp .env.example .env
# Edit .env and add your API keys (see Configuration section)

# 3. Start Ollama (in a separate terminal)
ollama serve

# 4. Run with Docker Compose
docker-compose up --build

# 5. Access the web interface
# Open http://localhost:5001 in your browser
```

### Option 2: Local Python Setup

For development or customization:

```bash
# 1. Clone the repository
git clone https://github.com/Manishmaurya89/Synthetic-Document-Factory.git
cd Synthetic-Document-Factory

# 2. Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Set up environment variables
cp .env.example .env
# Edit .env and add your API keys

# 5. Start Ollama
ollama serve

# 6. Run the application
python app.py

# 7. Access the web interface
# Open http://localhost:5001 in your browser
```

---

## ⚙️ Configuration

### Environment Variables

Create a `.env` file in the root directory with the following configuration:

```env
# Required: Unsplash API for image fetching
UNSPLASH_ACCESS_KEY=your_unsplash_access_key_here

# Ollama Configuration
OLLAMA_URL=http://host.docker.internal:11434  # For Docker
# OLLAMA_URL=http://localhost:11434           # For local setup

# Optional: Advanced Configuration
OUTPUT_DIR=./output
TEMP_DIR=./temp
LOG_LEVEL=INFO
```

### Getting API Keys

1. **Unsplash Access Key**:
   - Visit [Unsplash Developers](https://unsplash.com/developers)
   - Create a new application
   - Copy the Access Key to your `.env` file

2. **Ollama Setup**:
   - Install Ollama from [ollama.ai](https://ollama.ai/)
   - Pull a model: `ollama pull llama2` (or your preferred model)
   - Ensure Ollama is running before starting the application

### Security Note

**NEVER commit your `.env` file to version control!** The `.gitignore` is configured to exclude it automatically. For production deployments, use:
- GitHub Secrets for CI/CD pipelines
- Environment variables in cloud platforms
- Secret management services (AWS Secrets Manager, Azure Key Vault, etc.)

---

##  Project Structure

```
synthetic-document-factory/
├── fonts/                  # Multi-script font files
│   ├── devanagari/        # Hindi fonts
│   ├── arabic/            # Urdu fonts
│   └── telugu/            # Telugu fonts
├── stages/                 # Pipeline processing stages
│   ├── content_generation.py
│   ├── layout_engine.py
│   └── renderer.py
├── templates/              # Document templates
│   ├── report/
│   ├── article/
│   └── invoice/
├── web/                    # Web interface
│   ├── static/            # CSS, JS, images
│   └── templates/         # HTML templates
├── tests/                  # Test suite
│   ├── unit/
│   └── integration/
├── output/                 # Generated documents (git-ignored)
├── app.py                  # Flask web application
├── main_pipeline.py        # Pipeline orchestration
├── assembler.py            # Document assembly logic
├── pipeline_architecture.py # Architecture definitions
├── docker-compose.yml      # Docker orchestration
├── Dockerfile              # Container definition
├── requirements.txt        # Python dependencies
├── .env.example            # Environment template
└── README.md               # This file
```

---

##  Usage

### Web Interface

1. **Access Dashboard**: Navigate to `http://localhost:5001`
2. **Configure Generation**:
   - Select languages and their ratios
   - Choose document template
   - Set number of pages
   - Configure visual elements (tables, images)
3. **Generate**: Click "Generate Documents"
4. **Download**: Access generated PDFs from the output directory

### Command Line Interface

```bash
# Generate a single document
python main_pipeline.py --template report --languages en,hi,ur --pages 5

# Batch generation
python main_pipeline.py --batch --count 10 --template article

# Custom configuration
python main_pipeline.py --config custom_config.json
```

### Python API

```python
from main_pipeline import SyntheticDocumentFactory

# Initialize factory
factory = SyntheticDocumentFactory(
    languages=['en', 'hi', 'ur'],
    template='report',
    pages=5
)

# Generate document
document = factory.generate()
document.save('output/document.pdf')
```

---

##  Architecture

### Pipeline Stages

```
┌─────────────────┐
│ Configuration   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Content         │
│ Generation      │◄─── Ollama LLM
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Language        │
│ Distribution    │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Visual Elements │
│ Integration     │◄─── Unsplash API
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Layout Engine   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ PDF Rendering   │
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ Output Document │
└─────────────────┘
```

### Key Components

- **Content Generation**: AI-powered text generation using local LLMs
- **Language Distribution**: Enforces precise multilingual content ratios
- **Script Detection**: Automatically selects appropriate fonts based on language
- **Visual Integration**: Embeds tables, charts, and images contextually
- **PDF Renderer**: High-quality PDF generation with proper font embedding

---

##  Testing

```bash
# Run all tests
pytest

# Run specific test suite
pytest tests/unit/
pytest tests/integration/

# Run with coverage
pytest --cov=. --cov-report=html

# Run specific test
pytest tests/unit/test_pipeline.py::test_language_distribution
```

---

##  Troubleshooting

### Common Issues

<details>
<summary><b>Docker container fails to start</b></summary>

**Solution**:
- Ensure Docker is running: `docker --version`
- Check if ports are available: `lsof -i :5001`
- Verify `.env` file exists with valid API keys
- Check Docker logs: `docker-compose logs`
</details>

<details>
<summary><b>Ollama connection error</b></summary>

**Solution**:
- Verify Ollama is running: `curl http://localhost:11434`
- For Docker: Use `host.docker.internal:11434` instead of `localhost:11434`
- Check firewall settings
- Ensure a model is pulled: `ollama list`
</details>

<details>
<summary><b>Font rendering issues</b></summary>

**Solution**:
- Verify font files exist in `fonts/` directory
- Check font file permissions
- Ensure proper font mapping in configuration
- For custom fonts, add them to the `fonts/` directory
</details>

<details>
<summary><b>Image generation fails</b></summary>

**Solution**:
- Verify Unsplash API key is valid
- Check API rate limits
- Ensure internet connection is active
- Review API key permissions in Unsplash dashboard
</details>

---

## 🗺️ Roadmap

### Current Version: v1.0

### Planned Features

- [ ] **Additional Languages**: Chinese, Japanese, Korean, Russian
- [ ] **Advanced Templates**: Legal documents, scientific papers, presentations
- [ ] **OCR Ground Truth**: Automatic generation of text coordinates for OCR training
- [ ] **API Endpoints**: RESTful API for programmatic document generation
- [ ] **Cloud Storage**: Direct upload to S3, Google Cloud Storage, Azure Blob
- [ ] **Batch Processing**: Queue-based generation for large-scale datasets
- [ ] **Template Editor**: Visual template designer in web interface
- [ ] **Export Formats**: Support for DOCX, HTML, Markdown
- [ ] **Custom LLM Support**: Integration with OpenAI, Anthropic, and other providers
- [ ] **Performance Optimization**: GPU acceleration for rendering
- [ ] **Analytics Dashboard**: Generation statistics and usage metrics

---

##  Contributing

We welcome contributions from the community! Here's how you can help:

### Ways to Contribute

- 🐛 **Report Bugs**: Open an issue with detailed reproduction steps
- 💡 **Suggest Features**: Share your ideas for improvements
- 📖 **Improve Documentation**: Help make our docs clearer
- 🔧 **Submit PRs**: Fix bugs or implement new features
- 🌍 **Add Languages**: Contribute new language support
- 🎨 **Create Templates**: Design new document templates

### Development Setup

```bash
# Fork the repository
# Clone your fork
git clone https://github.com/YOUR_USERNAME/Synthetic-Document-Factory.git

# Create a feature branch
git checkout -b feature/amazing-feature

# Make your changes
# Add tests for new features

# Run tests
pytest

# Commit your changes
git commit -m "Add amazing feature"

# Push to your fork
git push origin feature/amazing-feature

# Open a Pull Request
```

### Code Style

- Follow PEP 8 for Python code
- Use type hints where applicable
- Write descriptive commit messages
- Add docstrings to functions and classes
- Include unit tests for new features

---

## License

This project is licensed under the **MIT License** - see the [LICENSE](LICENSE) file for details.

```
MIT License

Copyright (c) 2026 Manish Maurya

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.
```

---

##  Acknowledgments

- **Ollama Team** - For providing excellent local LLM infrastructure
- **Unsplash** - For high-quality, free-to-use images
- **Open Source Community** - For the amazing libraries and tools
- **Contributors** - Thank you to everyone who has contributed to this project!

---

##  Contact & Support

- **Author**: Manish Maurya
- **GitHub**: [@Manishmaurya89](https://github.com/Manishmaurya89)
- **Project Repository**: [Synthetic-Document-Factory](https://github.com/Manishmaurya89/Synthetic-Document-Factory)
- **Issue Tracker**: [Report Issues](https://github.com/Manishmaurya89/Synthetic-Document-Factory/issues)

### Getting Help

- 📚 Check the [documentation](#-documentation)
- 🐛 Search [existing issues](https://github.com/Manishmaurya89/Synthetic-Document-Factory/issues)
- 💬 Open a [new issue](https://github.com/Manishmaurya89/Synthetic-Document-Factory/issues/new)
- ⭐ Star the project if you find it useful!

---

<div align="center">

### ⭐ Star this repository if you find it helpful!

**Made with ❤️ by [Manish Maurya](https://github.com/Manishmaurya89)**

[⬆ Back to Top](#-synthetic-document-factory)

</div>
