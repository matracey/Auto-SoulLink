(function () {
  'use strict';

  document.addEventListener('submit', async function (event) {
    const form = event.target.closest && event.target.closest('form.mon-recovery');
    if (!form) return;
    event.preventDefault();
    const output = form.querySelector('output');
    const action = form.elements.action.value;
    const target = form.elements.target_key.value;
    if (!action) {
      output.textContent = 'Left unresolved. Automatic sync remains paused for this pair.';
      return;
    }
    if (action === 'rebind' && !target) {
      output.textContent = 'Choose a current Pokémon to match.';
      return;
    }
    const consequences = {
      rebind: 'Match this encounter to the selected Pokémon and resume the pair?',
      retire: 'Record this loss permanently and apply linked-death rules to the partner?',
      detach: 'Detach this pair and keep the surviving partner as an unpaired encounter?'
    };
    if (!consequences[action]) {
      output.textContent = 'Unknown resolution action.';
      return;
    }
    if (!window.confirm(form.dataset.name + '\n\n' + consequences[action])) return;
    const button = form.querySelector('button[type="submit"]');
    button.disabled = true;
    output.textContent = 'Applying resolution...';
    const run = /^\/runs\/[^/]+/.exec(location.pathname);
    try {
      const response = await fetch((run ? run[0] : '') + '/api/recovery', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          player: form.dataset.player, key: form.dataset.key,
          revision: form.dataset.revision, action: action, target_key: target
        })
      });
      const result = await response.json();
      output.textContent = response.ok && result.ok
        ? 'Resolved. The board updates on its next refresh.'
        : 'Not resolved: ' + (result.error || 'HTTP ' + response.status);
    } catch (error) {
      output.textContent = 'Could not confirm the result. Refresh the board before retrying. ' + error.message;
    } finally {
      button.disabled = false;
    }
  });
})();
