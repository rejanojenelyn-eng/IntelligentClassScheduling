// Log in links point to Flask's /login route (set in templates/landing.html).

// Mobile menu toggle
const nav = document.getElementById('nav');
document.querySelector('.menu-btn').addEventListener('click', () => nav.classList.toggle('open'));
nav.querySelectorAll('a').forEach(a => a.addEventListener('click', () => nav.classList.remove('open')));

// Fade-in on scroll
const items = document.querySelectorAll('.card, .feature, .step, .metric, .member, .persona, .flow-step, .shot');
items.forEach(el => el.classList.add('reveal'));
const io = new IntersectionObserver(entries => {
  entries.forEach(e => { if (e.isIntersecting) { e.target.classList.add('show'); io.unobserve(e.target); } });
}, { threshold: 0.15 });
items.forEach(el => io.observe(el));