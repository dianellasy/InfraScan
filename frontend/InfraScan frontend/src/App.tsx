
import { useState } from "react";

// import components needed to navigate bw pages
import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';

// import existing InfraScan pages
import Login from './pages/Login.tsx';
import Overview from './pages/Overview.tsx';
import Upload from './pages/Upload.tsx';
import Detections, { type Detection } from "./pages/Detections";
import DetectionReview from "./pages/DetectionReview";

// import existing webapp styling
import "./App.css";

function App() {
  // Store detection results so Upload and Detections can share data
  const [detections, setDetections] = useState<Detection[]>([]);

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
          element={
            <Upload onDetectionsLoaded={setDetections} />
          }
        />

        {/*
          "Detections" : view and review detected road issues

          URL: http://localhost:5173/detections
        */}
        <Route
          path="/detections"
          element={<Detections detections={detections} />}
        />
        {/*
        "Detection Review" : inspect and validate an individual detection

        URL: http://localhost:5173/detections/review
        */}
        <Route
          path="/detections/review"
          element={<DetectionReview />}
        />
      </Routes>
    </BrowserRouter>
  );
}

export default App;
