import { Icon } from "../components/icons";
import { PlannerForm } from "../components/planner-form";

export default function Home() {
  return <div className="home-page">
    <div className="home">
      <section className="intro">
        <p className="eyebrow">EUROPEAN AWAY DAYS · 2026 / 27</p>
        <h1>Follow Palace.<br /><em>Make a trip of it.</em></h1>
        <p className="lead">Pick a match and a UK city. We’ll line up the journey and a place to stay.</p>
        <ul className="features">
          <li><Icon name="plane" /><span><strong>Flights & trains</strong>Dated routes from major UK cities</span></li>
          <li><Icon name="bed" /><span><strong>Somewhere to stay</strong>Matched to your style and budget</span></li>
          <li><Icon name="pound" /><span><strong>Real prices</strong>You book direct with providers</span></li>
        </ul>
      </section>
      <PlannerForm />
    </div>
  </div>;
}
