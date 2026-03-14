import { useState } from "react";
import axios from "axios";

export default function RecipeForm() {
  const [query, setQuery] = useState("");
  const [result, setResult] = useState(null);

  const handleSubmit = async () => {
    const response = await axios.post("http://localhost:5001/api/recipe", {
      query,
      dietary: "None",
      health: "None",
      allergens: "",
      calories: 0,
      protein: 0,
      fat: 0
    });
    setResult(response.data);
  };

  return (
    <div>
      <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Enter recipe..." />
      <button onClick={handleSubmit}>Get Recipe</button>
      {result && (
        <div>
          <p>Latency: {result.latency}s</p>
          <p>Recommendation: {result.recommendation}</p>
        </div>
      )}
    </div>
  );
}
