
// import components needed to navigate bw pages
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';

// import existing InfraScan pages
import Login from './pages/Login.tsx';
import Overview from './pages/Overview.tsx';
import Upload from './pages/Upload.tsx';

// import existing webapp styling
import "./App.css";

function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/*
          Login" : the landing page for user authentication
          
          URL: http://localhost:5173/login
        */}
        <Route 
          path="/" 
          element={<Navigate to="/login" replace />} 
        />

        {/*
          Prototype Login" : the landing page for user authentication
          
          URL: http://localhost:5173/login
        */}
        <Route 
          path="/login" 
          element={<Login />} 
        />
    
        {/*
          "Overview" : the main workspace of landing page
          
          URL: http://localhost:5173/overview
        */}

        <Route 
          path="/overview" 
          element={<Overview />} 
        />

        {/*
          "Upload" : upload page for imgae-analysis
          
          URL: http://localhost:5173/upload
        */}

        <Route 
          path="/upload" 
          element={<Upload />}
        />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
