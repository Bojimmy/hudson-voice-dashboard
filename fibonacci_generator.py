def fibonacci(n):
    """
    Generate the first n Fibonacci numbers.
    
    :param n: The number of Fibonacci numbers to generate.
    :return: A list of the first n Fibonacci numbers.
    """
    fib_sequence = [0, 1]
    while len(fib_sequence) < n:
        fib_sequence.append(fib_sequence[-1] + fib_sequence[-2])
    return fib_sequence[:n]

def main():
    """
    Print the first 20 Fibonacci numbers.
    """
    print(fibonacci(20))

if __name__ == "__main__":
    main()