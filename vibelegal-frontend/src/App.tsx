import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { Layout } from './components/Layout';
import { Review } from './pages/Review';
import { Batch } from './pages/Batch';
import { Playbooks } from './pages/Playbooks';
import { Settings } from './pages/Settings';

function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<Navigate to="/review" replace />} />
          <Route path="review" element={<Review />} />
          <Route path="batch" element={<Batch />} />
          <Route path="playbooks" element={<Playbooks />} />
          <Route path="settings" element={<Settings />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}

export default App;
