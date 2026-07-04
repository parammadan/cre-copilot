import { Routes, Route } from 'react-router-dom';
import LandingPage from './components/LandingPage';
import Workspace from './pages/Workspace';

// "Launch Workspace" → full console, via the 2-step onboarding (connectors → confirmation).
const launchConsole = () => { window.location.href = '/console?onboard=1'; };

export default function App() {
  return (
    <Routes>
      <Route path="/" element={<LandingPage onLaunch={launchConsole} />} />
      {/* React workspace kept accessible directly, but not the default flow */}
      <Route path="/workspace" element={<Workspace />} />
    </Routes>
  );
}
