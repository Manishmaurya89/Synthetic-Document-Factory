document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('gen-form');
    const langCheckboxes = document.querySelectorAll('input[name="lang"]');
    const mixingContainer = document.getElementById('mixing-container');
    const ratioInputs = document.getElementById('ratio-inputs');
    const statusContainer = document.getElementById('status-container');
    const previewContainer = document.getElementById('preview-container');
    const loader = document.getElementById('loader');
    const pdfFrame = document.getElementById('pdf-frame');
    const downloadLink = document.getElementById('download-link');
    const fileInfo = document.getElementById('file-info');

    // Handle language selection changes to show/hide mixing ratios
    langCheckboxes.forEach(cb => {
        cb.addEventListener('change', updateMixingInputs);
    });


    function updateMixingInputs() {
        const selectedLangs = Array.from(langCheckboxes)
            .filter(cb => cb.checked)
            .map(cb => ({ code: cb.value, name: cb.parentElement.textContent.trim() }));

        if (selectedLangs.length > 1) {
            mixingContainer.classList.remove('hidden');
            ratioInputs.innerHTML = '';

            // Calculate default equal split
            const defaultShare = Math.floor(100 / selectedLangs.length);

            selectedLangs.forEach((lang, index) => {
                const share = (index === selectedLangs.length - 1)
                    ? 100 - (defaultShare * (selectedLangs.length - 1))
                    : defaultShare;

                const group = document.createElement('div');
                group.className = 'input-group';
                group.style.marginBottom = '10px';
                group.innerHTML = `
                    <label style="font-size: 0.8rem">${lang.name} (%)</label>
                    <input type="number" class="ratio-input" data-lang="${lang.code}" value="${share}" min="0" max="100">
                `;
                ratioInputs.appendChild(group);
            });
        } else {
            mixingContainer.classList.add('hidden');
        }
    }

    // Form Submission
    form.addEventListener('submit', async (e) => {
        e.preventDefault();

        const topic = document.getElementById('topic').value;
        const pages = document.getElementById('pages').value;
        const template = document.getElementById('template').value;
        const format = document.getElementById('format').value;

        const includeTables = document.getElementById('include-tables').checked;
        const includeImages = document.getElementById('include-images').checked;

        const languages = Array.from(langCheckboxes)
            .filter(cb => cb.checked)
            .map(cb => cb.value);

        if (languages.length === 0) {
            alert('Please select at least one language.');
            return;
        }

        const mixing_ratio = {};
        if (languages.length > 1) {
            let total = 0;
            document.querySelectorAll('.ratio-input').forEach(input => {
                const val = parseInt(input.value);
                mixing_ratio[input.dataset.lang] = val;
                total += val;
            });

            if (total !== 100) {
                alert('Total mixing ratio must sum to 100% (Current: ' + total + '%)');
                return;
            }
        } else {
            mixing_ratio[languages[0]] = 100;
        }

        // UI State: Loading
        statusContainer.classList.add('hidden');
        previewContainer.classList.add('hidden');
        loader.classList.remove('hidden');
        document.getElementById('generate-btn').disabled = true;

        try {
            const response = await fetch('/api/generate', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    topic,
                    pages,
                    template,
                    languages,
                    mixing_ratio,
                    format,
                    include_tables: includeTables,
                    include_images: includeImages
                })
            });

            const result = await response.json();

            if (result.success) {
                // UI State: Preview
                loader.classList.add('hidden');
                previewContainer.classList.remove('hidden');

                fileInfo.textContent = `${result.filename} (${result.page_count} pages, ${result.word_count} words)`;
                downloadLink.href = result.file_url;

                // Show preview if it's a PDF
                if (format === 'pdf') {
                    pdfFrame.src = `/api/preview/${result.filename}`;
                    pdfFrame.classList.remove('hidden');
                } else {
                    pdfFrame.classList.add('hidden');
                    // For non-PDF, maybe just show a download button and a success message
                    statusContainer.innerHTML = `
                        <div class="success-state">
                            <div class="icon-pulse"></div>
                            <p>${result.filename} generated successfully!</p>
                            <div style="margin-top: 20px;">
                                <button type="button" onclick="window.location.reload()" class="btn-small" style="cursor: pointer; background-color: #6c757d; border: none; margin-right: 10px;">↻ Refresh</button>
                                <a href="${result.file_url}" class="btn-small" style="display: inline-block">Download ${format.toUpperCase()}</a>
                            </div>
                        </div>
                    `;
                    statusContainer.classList.remove('hidden');
                }
            } else {
                throw new Error(result.errors.join(', '));
            }
        } catch (error) {
            alert('Generation Failed: ' + error.message);
            loader.classList.add('hidden');
            statusContainer.classList.remove('hidden');
        } finally {
            document.getElementById('generate-btn').disabled = false;
        }
    });
});
