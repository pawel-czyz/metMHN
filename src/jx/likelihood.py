import numpy as np

from jx.kronvec import kronvec_sync, kronvec_met, kronvec_prim, kronvec_seed, kronvec, kron_diag, obs_states
import Utilityfunctions as utils
import jax.numpy as jnp
from jax import jit, lax, vmap
from functools import partial
from jx import vanilla as mhn


def f1(s: jnp.array, p: jnp.array, m: jnp.array) -> tuple[jnp.array, jnp.array, jnp.array, float]:
    s = s.reshape((-1, 2), order="C")
    p = p.reshape((-1, 2), order="C")
    m = m.reshape((-1, 2), order="C")

    z = p[:, 1].sum()

    s = s.flatten(order="F")
    p = p.flatten(order="F")
    m = m.flatten(order="F")

    return s, p, m, z



def f2(s: jnp.array, p: jnp.array, m: jnp.array) -> tuple[jnp.array, jnp.array, jnp.array, float]:
    s = s.reshape((-1, 2), order="C")
    p = p.reshape((-1, 2), order="C")
    m = m.reshape((-1, 2), order="C")

    z = m[:, 1].sum()

    s = s.flatten(order="F")
    p = p.flatten(order="F")
    m = m.flatten(order="F")

    return s, p, m, z



def f3(s: jnp.array, p: jnp.array, m: jnp.array) -> tuple[jnp.array, jnp.array, jnp.array, float]:
    s = s.reshape((-1, 4), order="C")
    p = p.reshape((-1, 4), order="C")
    m = m.reshape((-1, 4), order="C")

    z = s[:, 3].sum() + p[:, [1, 3]].sum() + m[:, [2, 3]].sum()

    s = s.flatten(order="F")
    p = p.flatten(order="F")
    m = m.flatten(order="F")

    return s, p, m, z



def t12(s: jnp.array, p: jnp.array, m: jnp.array) -> tuple[jnp.array, jnp.array, jnp.array, float]:
    s = s.reshape((-1, 2), order="C")
    p = p.reshape((-1, 2), order="C")
    m = m.reshape((-1, 2), order="C")

    z = s[:, 0].sum() + p.sum() + m.sum()

    s = s.flatten(order="F")
    p = p.flatten(order="F")
    m = m.flatten(order="F")

    return s, p, m, z



def t3(s: jnp.array, p: jnp.array, m: jnp.array) -> tuple[jnp.array, jnp.array, jnp.array, float]:
    s = s.reshape((-1, 4), order="C")
    p = p.reshape((-1, 4), order="C")
    m = m.reshape((-1, 4), order="C")

    z = s.sum() + p.sum() + m.sum()

    s = s.flatten(order="F")
    p = p.flatten(order="F")
    m = m.flatten(order="F")

    return s, p, m, z



def z3(s: jnp.array) -> tuple[jnp.array, float]:
    s = s.reshape((-1, 4), order="C")

    z = s[:, 3].sum()

    s = s.flatten(order="F")

    return s, z


def deriv_no_seed(i, val, x, y, log_theta, state, n):

    z_sync = jnp.multiply(x, kronvec_sync(log_theta=log_theta,
                                p=y, i=i, state=state))
    z_prim = jnp.multiply(x, kronvec_prim(log_theta=log_theta,
                                p=y, i=i, state=state))
    z_met = jnp.multiply(x, kronvec_met(log_theta=log_theta,
                            p=y, i=i, state=state))
    val = val.at[-1].set(z_met.sum())

    def body_fun(j, l_val):

        _z_sync, _z_prim, _z_met, _z = lax.switch(
            state.at[2*j].get() + 2 * state.at[2*j+1].get(),
            [
                lambda s, p, m: (s, p, m, 0.),
                f1,
                f2,
                f3
            ],
            l_val[0],
            l_val[1],
            l_val[2],

        )
        return _z_sync, _z_prim, _z_met, l_val[3].at[j].set(_z)

    z_sync, z_prim, z_met, val = lax.fori_loop(
        lower=0,
        upper=i,
        body_fun=body_fun,
        init_val=(z_sync, z_prim, z_met, val)
    )

    z_sync, z_prim, z_met, _z = lax.switch(
        state.at[2*i].get() + 2 * state.at[2*i+1].get(),
        [
            lambda s, p, m: (
                s, p, m, (jnp.sum(s) + jnp.sum(p) + jnp.sum(m))),
            t12,
            t12,
            t3
        ],
        z_sync,
        z_prim,
        z_met,
    )
    val = val.at[i].set(_z)

    z_sync, z_prim, z_met, val = lax.fori_loop(
        lower=i+1,
        upper=n,
        body_fun=body_fun,
        init_val=(z_sync, z_prim, z_met, val)
    )

    return val


def x_partial_Q_y(log_theta: jnp.array, x: jnp.array, y: jnp.array, state: jnp.array, diagnosis: bool=True) -> jnp.array:
    """This function computes x \partial Q y with \partial Q the Jacobian of Q w.r.t. all thetas
    efficiently using the shuffle trick (sic!).

    Args:
        log_theta (jnp.array):  Logarithmic theta values of the MHN, if diagnosis == true, then the log theta entries 
                                are expected to be scaled with the diagnosis rates beforehand 
        x (jnp.array):          x vector to multiply with from the left. Length must equal the number of
                                nonzero entries in the state vector.
        y (jnp.array):          y vector to multiply with from the right. Length must equal the number of
                                nonzero entries in the state vector.
        state (jnp.array):      Binary state vector, representing the current sample's events.
        diagnosis (bool):       Wether to calculate the derivative of the diagnosis effects

    Returns:
        tuple: x \partial_(\Theta_{ij}) Q y,  x \partial_(\Theta_{dj}) Q yfor i, j = 1, ..., n+1
    """
    n = log_theta.shape[0] - 1
    z = jnp.zeros(shape=(n + 1, n + 1))
    # This line leads to an OOM-error for states with > 24 events
    # z = z.at[:-1,:].set(vmap(deriv_no_seed, in_axes=(0,0, None, None, None, None, None), 
    #                     out_axes=0)(jnp.arange(n, dtype=jnp.int64), z[:-1,:], x, y, log_theta, state, n))
    # Less performant but robust workaround:
    def init_z(j, val):
        val = val.at[j, :].set(deriv_no_seed(j, val[:, j], x, y, log_theta, state, n))
        return val
        
    z = lax.fori_loop(lower=0, upper=n, body_fun=init_z, init_val=z) 
    z_seed = jnp.multiply(x, kronvec_seed(log_theta=log_theta, p=y, state=state))
    z = z.at[-1, -1].set(z_seed.sum())

    def body_fun(j, val):

        _z_seed, _z = lax.switch(
            state.at[2*j].get() + 2 * state.at[2*j+1].get(),
            branches=[
                lambda s: (s, 0.),
                lambda s: (
                    s.reshape((-1, 2), order="C").flatten(order="F"), 0.),
                lambda s: (
                    s.reshape((-1, 2), order="C").flatten(order="F"), 0.),
                z3
            ],
            operand=val[0]
        )
        return _z_seed, val[1].at[-1, j].set(_z)

    z_seed, z = lax.fori_loop(
        lower=0,
        upper=n,
        body_fun=body_fun,
        init_val=(z_seed, z)
    )

    # The derivative of the i-th diagnosis rate is given by the negative sum of the off-diagonal entries in the i-th column of d-theta 
    d_diag = lax.cond(diagnosis,
                      lambda z: -1. * jnp.sum(z, axis=0) + jnp.diagonal(z),
                      lambda z: jnp.zeros(z.shape[0]),
                      z
                    )
    return z, d_diag

@partial(jit, static_argnames=["transpose"])
def R_i_inv_vec(log_theta: jnp.array, x: jnp.array, lam: jnp.array,  state: jnp.array, transpose: bool = False) -> jnp.array:
    """This computes R_i^{-1} x = (\lambda_i I - Q)^{-1} x

    Args:
        log_theta (np.array): Log values of the theta matrix
        x (np.array): Vector to multiply with from the right. Length must equal the 2 to the number of
        nonzero entries in the state vector.
        lam (jnp.array): Rate \lambda_i of i-th sampling. Has to be set to 1. if the diagnosis formulism is used
        state (jnp.array): Binary state vector, representing the current sample's events.
        transpose (bool): calculate R^(-T)p else calc R^(-1)p
    Returns:
        jnp.array: R_i^{-1} x
    """
    n = log_theta.shape[0] - 1
    state_size = np.log2(x.shape[0]).astype(int)
    lidg = -1 / (kron_diag(log_theta=log_theta, state= state, p_in=x) - lam)
    y = lidg * x
    y = lax.fori_loop(
        lower=0,
        upper=state_size+1,
        body_fun=lambda _, val: lidg * (kronvec(log_theta=log_theta, p=val,
                                                state=state, diag=False, transpose=transpose) + x),
        init_val=y
    )

    return y

@partial(jit, static_argnames=["n_prim", "n_met"])
def _lp_coupled(log_theta_fd: jnp.array, log_theta_fd_prim: jnp.array, log_theta_sd: jnp.array, state_joint: jnp.array, 
                n_prim: int, n_met: int, ) -> jnp.array:
    """
    Evaluates the log probability of seeing coupled genotype data in state "state"
    Args:
        log_theta_fd (jnp.array):       Theta_matrix with logarithmics entries, scaled by effects on second diagnosis
        log_theta_sd (jnp.array):       Theta_matrix with logarithmic entries, scaled by effects on first diagnosis
        log_theta_prim (jnp.array):     Copy of log_theta_fd,but  with off-diagonal entries in the 
                                        last column set to 0 
        state_joint (jnp.array):        Bitstring, genotypes of PT and MT 
        n_prim (int):                   Number of active events in PT
        n_met (int):                    NUmber of active events in MT
    Returns:
        jnp.array: log(P(state))
    """
    p0 = jnp.zeros(2**(n_prim + n_met - 1))
    p0 = p0.at[0].set(1.)
    pTh1_joint = R_i_inv_vec(log_theta_fd, p0, 1., state_joint, transpose = False)
    
    p0 = jnp.zeros(2**n_prim)
    p0 = p0.at[0].set(1.)
    prim = state_joint.at[0::2].get()
    pTh1_marg = mhn.R_inv_vec(log_theta_fd_prim, p0, 1., prim, transpose = False)
    nk = pTh1_marg.at[-1].get()

    # Select the states where x = prim and z are compatible with met 
    poss_states = obs_states((n_prim + n_met - 1), state_joint, True)
    poss_states_inds = jnp.where(poss_states, size=2**(n_met-1))[0]
    # Prim conditional distribution at first sampling, to be used as starting dist for second sampling
    pTh1_cond_obs = pTh1_joint.at[poss_states_inds].get()
    # States where the Seeding didn't happen aren't compatible with met and get probability 0
    pTh1_cond_obs = jnp.append(jnp.zeros(2**(n_met-1)), pTh1_cond_obs)
    pTh1_cond_obs *= 1/nk
    met = jnp.append(state_joint.at[1::2].get(), 1)
    pTh2 = mhn.R_inv_vec(log_theta_sd, pTh1_cond_obs, 1, met)
    return jnp.log(nk) + jnp.log(pTh2.at[-1].get())


@partial(jit, static_argnames=["n_prim"])
def _lp_prim_obs(log_theta_prim_fd: jnp.array, state_prim: jnp.array, n_prim: int) -> jnp.array:
    """Calculates the marginal likelihood of only observing a PT at first sampling with genotype state_prim
    
    Args:
        log_theta_prim_fd (jnp.array):      Theta matrix for diagnosis formalism with logarithmic entries. 
                                            The off-diagonal entries of the last column are set to 0.
        state_prim (jnp.array):                  Bitstring of length 2*n+1, observed genotype of tumor(pair)
        n_prim (int):                       Number of active events in the PT
        
    Returns:
        jnp.array: log(P(state_prim; theta))
    """
    p0 = jnp.zeros(2**n_prim)
    p0 = p0.at[0].set(1.)
    pTh = mhn.R_inv_vec(log_theta_prim_fd, p0, 1.,  state_prim, False)
    return jnp.log(pTh.at[-1].get())


@partial(jit, static_argnames=["n_met"])
def _lp_met_obs(log_theta_fd: jnp.array, log_theta_sd: jnp.array, state_met: jnp.array, n_met: int):
    """Calculates the marginal likelihood of only observing a MT at second sampling with genotype state_met
    
    Args:
        log_theta_fd (jnp.array):   Theta matrix with logarithmic entries, scaled by fd_effects 
        log_theta_sd (jnp.array):   Theta matrix with logarithmic entries, scaled by sd_effects 
        state_met (jnp.array):      Bitstring, genotype of MT
        n_met (int):                Number of active events in the MT

    Returns:
        jnp.array: log(P(state_met; theta))
    """
    p0 = jnp.zeros(2**n_met)
    p0 = p0.at[0].set(1.)
    pTh1 = mhn.R_inv_vec(log_theta_fd, p0, 1., state_met, False)
    pTh2 = mhn.R_inv_vec(log_theta_sd, pTh1, 1., state_met, False)
    return jnp.log(pTh2.at[-1].get())


@partial(jit, static_argnames=["n_met"])
def _grad_met_obs(log_theta_fd: jnp.array, log_theta_sd: jnp.array, state_met: jnp.array, n_met: int) -> tuple:
    """ Gradient of lp_met_obs for a single sample

    Args:
        log_theta_fd (jnp.array):   Theta matrix with logarithmic entries, scaled by first diagnosis effects (fd)
        log_theta_sd (jnp.array):   Theta matrix with logarithmic entries, scaled by second diagnosis effects (sd)
        state_met (jnp.array):      Bitstring, representing the current sample's genotype
        n_met (int):                Number of active events in current sample

    Returns:
        tuple:                      Marginal likelihood, gradient wrt. theta, gradient wrt. fd, gradient wrt. sd
    """
    p0 = jnp.zeros(2**n_met)
    p0 = p0.at[0].set(1.)
    R_1_inv_p_0 = mhn.R_inv_vec(
        log_theta=log_theta_fd,
        x=p0,
        lam=1.,
        state=state_met)

    R_2_inv_p_1 = mhn.R_inv_vec(
        log_theta=log_theta_sd,
        x=R_1_inv_p_0,
        lam=1.,
        state=state_met,
    )

    score = jnp.log(R_2_inv_p_1.at[-1].get())
    
    lhs = jnp.zeros_like(p0)
    lhs = lhs.at[-1].set(1. / R_2_inv_p_1.at[-1].get())
    lhs = mhn.R_inv_vec(log_theta_sd, lhs, 1., state_met, True)
    dTh_1, d_sd = mhn.x_partial_Q_y(log_theta_sd, lhs, R_2_inv_p_1, state_met)
    
    lhs = mhn.R_inv_vec(log_theta_fd, lhs, 1, state_met, True)
    dTh_2, d_fd = mhn.x_partial_Q_y(log_theta_fd, lhs, R_1_inv_p_0, state_met) 
    return score, dTh_1 + dTh_2, d_fd, d_sd

@partial(jit, static_argnames=["n_prim"])
def _grad_prim_obs(log_theta_prim_fd: jnp.array, state_prim: jnp.array, n_prim: int) -> tuple:
    """ gradient of lp_prim_obs for a single sample

    Args:
        log_theta_prim_sd (jnp.array):  Log values of the theta matrix.
        state_prim (jnp.array):         Binary state vector, representing the current sample's events.
        n_prim (int):                   Number of active events in the PT
    Returns:
        tuple:                          Marginal Likelihood, gradient wrt. theta, gradient wrt. diagnosis effects
    """
    p0 = jnp.zeros(2**n_prim)
    p0 = p0.at[0].set(1.)
    p_theta= mhn.R_inv_vec(log_theta_prim_fd, p0, 1., state_prim)
    score = jnp.log(p_theta.at[-1].get())
    
    lhs = jnp.zeros_like(p0)
    lhs = lhs.at[-1].set(1/p_theta.at[-1].get())
    lhs = mhn.R_inv_vec(log_theta_prim_fd, lhs, 1., state_prim, transpose = True)
    dth, d_diag_effects = mhn.x_partial_Q_y(log_theta_prim_fd, lhs, y=p_theta, state=state_prim)
    dth = dth.at[:-1, -1].set(0.0)  # Derivative of constant is 0.
    return score, dth, d_diag_effects 


@partial(jit, static_argnames=["n_prim", "n_met"])
def _g_coupled(log_theta_fd: jnp.array, log_theta_fd_prim: jnp.array, log_theta_sd: jnp.array, 
               state_joint: jnp.array, n_prim: int, n_met: int) -> tuple:
    """Calculates the likelihood and gradients for a coupled datapoint (PT and MT genotype known)

    Args:
        log_theta_fd (jnp.array):       Theta matrix with logarithmic entries, scaled by effects on first diagnosis
        log_theta_fd_prim (jnp.array):  Copy of log_theta_fd, with off diagonal entries of the last column set to 0.
        log_theta_sd (jnp.array):       Theta matrix with logarithmic entries, scaeld by effects on second diagnosis
        state_joint (jnp.array):        Bitstring, genotypes of PT and MT of the same patient
        n_prim (int):                   Number of active events in the PT
        n_met (int):                    Number of active events in the MT

    Returns:
        tuple: score, grad wrt. to theta, grad wrt. fd_effects, grad wrt. sd_effects
    """
    prim = state_joint.at[0::2].get()
    met = jnp.append(state_joint.at[1::2].get(), 1)
    p0 = jnp.zeros(2**(n_prim + n_met - 1))
    p0 = p0.at[0].set(1.)
    
    # Joint and met-marginal distribution at first sampling
    pTh1_joint = R_i_inv_vec(log_theta_fd, p0, 1., state_joint, transpose = False)
    p0 = jnp.zeros(2**n_prim)
    p0 = p0.at[0].set(1.)
    g_1, d_fd1, pTh1_marg = mhn.gradient(log_theta_fd_prim, 1., prim, p0)
    nk = pTh1_marg.at[-1].get()
    
    # Select the states where x = prim and z are compatible with met 
    poss_states = obs_states((n_prim + n_met - 1), state_joint, True)
    poss_states_inds = jnp.where(poss_states, size=2**(n_met-1))[0]
    # Prim conditional distribution at first sampling, to be used as starting dist for second sampling
    pTh1_cond_obs = pTh1_joint.at[poss_states_inds].get()
    # States where the Seeding didn't happen aren't compatible with met and get probability 0
    pTh1_cond_obs = jnp.append(jnp.zeros(2**(n_met-1)), pTh1_cond_obs)
    pTh1_cond_obs *= 1/nk
    
    # Derivative of pTh2 = M(I-Q_sd)^(-1)pth1_cond
    g_2, d_sd1, pTh2_marg = mhn.gradient(log_theta_sd, 1., met, pTh1_cond_obs)
    # lhs = (pD/pTh_2)^T M(I-Q_sd)^(-1)
    q = jnp.zeros(2**n_met)
    q = q.at[-1].set(1/pTh2_marg.at[-1].get())
    q = mhn.R_inv_vec(log_theta_sd, q, 1., met, transpose = True)
    lhs = jnp.zeros(2**(n_prim + n_met - 1))
    lhs = lhs.at[poss_states_inds].set(q.at[2**(n_met - 1):].get())
    
    # Derivative of pth1_cond
    q = R_i_inv_vec(log_theta_fd, lhs, 1., state_joint, transpose = True)
    g_3, d_fd2 = x_partial_Q_y(log_theta_fd, q, pTh1_joint, state_joint)
    g_3 /= nk
    d_fd2 /= nk
    q = jnp.zeros(2**(n_prim))
    q = q.at[-1].set(1/nk)
    q = mhn.R_inv_vec(log_theta_fd_prim, q, 1., prim, transpose = True)
    g_4, d_fd3 = mhn.x_partial_Q_y(log_theta_fd_prim, q, pTh1_marg, prim)
    g_4 *= -1.
    d_fd3 *= -1.
    score =  jnp.log(nk) + jnp.log(pTh2_marg.at[-1].get())
    return score, g_1 + g_2 + g_3 + g_4, d_fd1 + d_fd2 + d_fd3, d_sd1