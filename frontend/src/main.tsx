import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import 'bootstrap/dist/css/bootstrap.min.css';
import './styles/app.css';

import App from './App';

const container = document.getElementById('root');
if (!container) {
  throw new Error('#root が見つかりません。');
}

createRoot(container).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
