'use strict';
const choices = document.querySelectorAll('.demo-task select');
const status = document.querySelector('#demo-result');
for (const choice of choices) {
  choice.addEventListener('change', () => {
    const selected = choice.options[choice.selectedIndex].text;
    status.textContent = `${choice.dataset.task} now uses ${selected}. Other tasks are unchanged.`;
    const row = choice.closest('.demo-task');
    row.classList.remove('changed');
    requestAnimationFrame(() => row.classList.add('changed'));
  });
}
const copy = document.querySelector('#copy-command');
if (navigator.clipboard && window.isSecureContext) {
  copy.hidden = false;
  copy.addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(document.querySelector('#install-command').textContent);
      document.querySelector('#copy-status').textContent = 'Build commands copied.';
      copy.textContent = 'Copied';
    } catch {
      document.querySelector('#copy-status').textContent = 'Copy unavailable. Select the commands to copy them manually.';
      copy.textContent = 'Select text to copy';
    }
  });
}
