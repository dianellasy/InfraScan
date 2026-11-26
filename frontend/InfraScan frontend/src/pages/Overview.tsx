import "../App.css";

function Overview() {

  // PROTOTYPE: no authentication implemented yet.
  const handleLogout = () => {
    // navigate to login page after logout
    window.location.hash = "#/";
  };

  return (
    <main className="app-shell">
      {/* LOGOUT BUTTON */}
      <header className="app-header">

        <span className="app-header-label">
          InfraScan Workspace
        </span>

        {/*
          Logout button : navigates directly to login page.
        */}
        <button
          type="button"
          className="app-header-logout"
          onClick={handleLogout}
        >
          <span aria-hidden="true">↪</span>
          Logout
        </button>
      </header>

      {/* INTRODUCTION SECTION */}
      <section className="intro">

        <p className="eyebrow">
          INFRASCAN WORKSPACE
        </p>

        <h1>
          Road Infrastructure Monitoring
        </h1>

        <p>
          Infrascan helps identify and review road
          infrastructure issues using computer vision.
        </p>

      </section>

      {/*G GET STARTED SECTION */}
      <section className="panel">

        <h2>
          Start an Infrastructure Inspection
        </h2>

        <p>
          Upload a road image to analyze potential
          infrastructure issues and review detected results.
        </p>

        {/* Navigates directly to the upload page. */}
        <a
          href="#/"
          className="primary-button"
        >
          Start Scanning
        </a>

      </section>
  
    </main>
  );
}

export default Overview;