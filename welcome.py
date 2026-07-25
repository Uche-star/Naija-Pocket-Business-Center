try:
    from .ada_services import ada_welcome, ada_services
    from .nora_services import nora_welcome, nora_services
except ImportError:
    from ada_services import ada_welcome, ada_services
    from nora_services import nora_welcome, nora_services


def start_welcome():
    print("===================================")
    print("     NAIJA POCKET BUSINESS CENTER")
    print("===================================")

    print()
    print("Welcome! How we fit help you today?")
    print()

    print("Choose your worker:")
    print("1. Ada - Your Business Center Girl")
    print("2. Nora - Your Company Secretary & Personal Assistant")
    print()

    choice = input("Enter 1 for Ada or 2 for Nora: ")

    if choice == "1":
        ada_welcome()
        print()
        ada_services()

    elif choice == "2":
        nora_welcome()
        print()
        nora_services()

    else:
        print()
        print("Sorry, that option no dey available.")


if __name__ == "__main__":
    start_welcome() 
