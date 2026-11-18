import { BrowserRouter, Routes, Route, Link } from 'react-router-dom';
import "../App.css";
import Upload from './Upload.tsx';

function Overview() {
  return (
    <div>
      <h1>Welcome to the Underground</h1>
      <Link to="/upload">Start Scanning</Link>

      <Routes>
        <Route path="/upload" element={<Upload />} />
      </Routes>
    </div>
  );
}

export default Overview;