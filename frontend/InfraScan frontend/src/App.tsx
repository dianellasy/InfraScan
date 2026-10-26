

function App() {
  
  const callTestEndpoint = async () => {
    const respose = await fetch("http://127.0.0.1:8000/test")
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
