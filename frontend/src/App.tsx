import { Routes, Route, useNavigate } from 'react-router-dom';
import LandingPage from './components/LandingPage';
import Workspace from './pages/Workspace';

export default function App() {
  const navigate = useNavigate();
  return (
    <Routes>
      <Route path="/" element={<LandingPage onLaunch={() => navigate('/workspace')} />} />
      <Route path="/workspace" element={<Workspace />} />
    </Routes>
  );
}
