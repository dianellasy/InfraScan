import "../App.css";

/** Landing page after sign-in: what InfraScan does and where to start. */
function Overview() {
  return (
    <div className="overview-page">
      {/* INTRODUCTION SECTION */}
      <section className="intro">
        <p className="eyebrow">INFRASCAN WORKSPACE</p>
        <h1>Road Infrastructure Monitoring</h1>
        <p>
          Infrascan helps identify and review road
          infrastructure issues using computer vision.
        </p>
      </section>

      {/* GET STARTED SECTION */}
      <section className="panel">
        <h2>Start an Infrastructure Inspection</h2>
        <p>
          Upload a road image or dashcam video to analyze potential
          infrastructure issues and review detected results.
        </p>
        <a href="#/" className="primary-button">
          Start Scanning
        </a>
      </section>

      {/* WHERE TO GO NEXT */}
      <section className="panel">
        <h2>Review what was found</h2>
        <p>
          Every detection is grouped into a road issue. Browse them on the
          issue map, or work through the issues that need a human decision.
        </p>
        <div className="page-navigation">
          <a href="#/map" className="navigation-button">
            → Issue map
          </a>
          <a href="#/review" className="navigation-button">
            → Review queue
          </a>
        </div>
      </section>
    </div>
  );
}

export default Overview;
