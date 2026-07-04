import { Routes, Route } from 'react-router-dom';
import LandingPage from './components/LandingPage';
import Workspace from './pages/Workspace';

// "Launch Workspace" opens the full operations console (the complete existing app).
const launchConsole = () => { window.location.href = '/console'; };

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage onLaunch={launchConsole} />} />
      {/* React workspace kept accessible directly, but not the default flow */}
      <Route path="/workspace" element={<Workspace />} />
    </Routes>
  );
}
