const demoButton = document.querySelector('#demo-toggle');
const demoImage = document.querySelector('#demo-image');
demoButton.addEventListener('click', () => {
  const playing = demoButton.getAttribute('aria-pressed') !== 'true';
  demoButton.setAttribute('aria-pressed', String(playing));
  demoButton.textContent = playing ? 'Stop timelapse ■' : 'Play timelapse ↗';
  demoImage.src = playing ? 'timelapse.gif' : 'prototype.png';
  demoImage.alt = playing
    ? 'Accelerated recording of an agent building a prototype through the Godot editor.'
    : 'A collect-and-dodge prototype with a player, enemy and five collectibles.';
});
