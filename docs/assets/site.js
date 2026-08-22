(() => {
  const body = document.body;
  const menu = document.querySelector('#mobile-menu');
  const menuButton = document.querySelector('.menu-button');
  const menuClose = document.querySelector('.menu-close');
  let lastFocused = null;

  const closeMenu = () => {
    if (!menu || menu.hidden) return;
    menu.hidden = true;
    body.classList.remove('menu-open');
    menuButton?.setAttribute('aria-expanded', 'false');
    lastFocused?.focus();
  };

  const openMenu = () => {
    if (!menu) return;
    lastFocused = document.activeElement;
    menu.hidden = false;
    body.classList.add('menu-open');
    menuButton?.setAttribute('aria-expanded', 'true');
    menuClose?.focus();
  };

  menuButton?.addEventListener('click', openMenu);
  menuClose?.addEventListener('click', closeMenu);
  menu?.querySelectorAll('a').forEach((link) => link.addEventListener('click', closeMenu));

  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') closeMenu();
  });

  document.querySelectorAll('[data-expand-target]').forEach((button) => {
    button.addEventListener('click', () => {
      const panel = document.getElementById(button.dataset.expandTarget);
      if (!panel) return;
      const willOpen = button.getAttribute('aria-expanded') !== 'true';
      button.setAttribute('aria-expanded', String(willOpen));
      panel.hidden = !willOpen;
      const company = button.getAttribute('aria-label')?.replace(/^(Show|Hide) analysis for /, '');
      if (company) {
        button.setAttribute('aria-label', `${willOpen ? 'Hide' : 'Show'} analysis for ${company}`);
      }
    });
  });

  const toast = document.querySelector('#toast');
  let toastTimer;
  document.querySelectorAll('[data-coming-soon]').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      if (!toast) return;
      toast.textContent = `${link.dataset.comingSoon} is coming soon.`;
      toast.hidden = false;
      window.clearTimeout(toastTimer);
      toastTimer = window.setTimeout(() => { toast.hidden = true; }, 3000);
    });
  });

  const form = document.querySelector('#newsletter-form');
  const status = document.querySelector('#form-status');
  form?.addEventListener('submit', (event) => {
    event.preventDefault();
    if (!form.reportValidity()) return;
    if (status) status.textContent = 'Email delivery is coming soon—your address was not submitted.';
  });
})();
