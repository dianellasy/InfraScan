

import { apiBaseUrl } from "./api/endpoints.ts";

function App() {
  
  const callTestEndpoint = async () => {
    const respose = await fetch(`${apiBaseUrl}/test`)
    const data = await respose
    if (data.ok) {
      console.log("endpoint good" + data)

    } else {
      console.error("cannot fetch endpoint");
    }

  } 

  return (
    <>
    <p>InfraScan</p>
    <button
      onClick={callTestEndpoint}
    >test</button>
    </>)
}

export default App
