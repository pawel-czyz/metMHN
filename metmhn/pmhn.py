from metmhn.jx.likelihood import _lp_prim_obs, _lp_prim_obs_az


def _stratify(states, covariates):
    pass


def generate_loglikelihood_function(states):
    """Generates the loglikelihood function.

    Args:
        states: binary array of shape (n_samples, n_genes)

    Returns:
        the loglikelihood function, mapping the model parameters
            log_theta (shape (n_genes, n_genes))
            log_omega (shape (n_genes,))
        to the loglikelihood on the data set `states`
    """

    def loglike(log_theta, log_omega) -> float:
        """The loglikelihood function.

        Args:
            log_theta: shape (n_genes, n_genes)
            log_omega: shape (n_genes,)

        Returns:
            loglikelihood
        """

    return loglike
