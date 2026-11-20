import { useState, type FormEvent } from "react";
import{ useNavigate } from "react-router-dom";

import infrascanLogo from "../assets/infrascan.svg";
import "../App.css";

function Login() {
    // navigate to another page
    const navigate = useNavigate();

    // state variables to store user input
    const [email, setEmail] = useState("");
    const [password, setPassword] = useState("");

    // stores validation msg when incomplete
    const [error, setError] = useState("");

    // handles form submission when user clicks login button
    function handleSubmit(event: FormEvent<HTMLFormElement>) {
        // prevents browser from refreshing the page when form is submitted
        event.preventDefault();
        
        // checks if email and password are not empty
        if (email.trim() === "" || password.trim() === "") {
            setError("Please enter both email and password.");
            return;
        }

        // clear any previous error messages
        setError("");


        // PROTOTYPE: In a real application, you would send the username and password to the server for authentication.
        // navigate to the overview page after successful login
        navigate("/overview");
    }

    return (
        <main className="login-page">
            <section className="login-card">

            {/* InfraScan logo */}
            <div className="login-brand">
                <img src={infrascanLogo} alt="" />
                <h1>InfraScan</h1>
            </div>

            {/* Introduction */}
            <div className="login-heading">
                <h2>Welcome Back!</h2>
                <p>
                    Sign in to access your InfraScan account.
                </p>
            </div>

            {/* Login Form */}
            <form onSubmit={handleSubmit}>
                <div className="login-field">
                    <label htmlFor="email">Email address</label>

                    <input
                        id="email"
                        type="email"
                        placeholder="name@example.com"
                        autoComplete="email"
                        value={email}
                        onChange={(event) => setEmail(event.target.value)}
                        required
                    />
                </div>

                <div className="login-field">
                    <label htmlFor="password">Password</label>

                    <input
                        id="password"
                        type="password"
                        placeholder="Enter your password"
                        autoComplete="current-password"
                        value={password}
                        onChange={(event) => setPassword(event.target.value)}
                        required
                    />
                </div>

                {/* Validation Message */}
                {error && (
                    <p className="login-error" role="alert">
                        {error}
                    </p>
                )}


                {/* Submit the form */}
                <button className="login-submit" type="submit">
                    <span>Sign In</span>
                </button>

                </form>

                {/* Demo for prototype */}
                <p className="login-demo-note">
                    Prototype Demo: Use any email and password to log in.
                </p>
            </section>
        </main>
    );
}


export default Login;