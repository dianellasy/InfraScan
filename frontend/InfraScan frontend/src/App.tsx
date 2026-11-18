import { BrowserRouter, Routes, Route, Link } from 'react-router-dom';
import "./App.css";
import Overview from './pages/Overview.tsx';

function App() {
  return (
    <BrowserRouter>
      <Link to="/overview">Enter Workspace</Link>

      <Routes>
        <Route path="/overview" element={<Overview />} />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
